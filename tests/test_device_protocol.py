import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.database import SessionLocal
from app.config import get_settings
from app.models import DeviceCommand, Employee, Punch, WebhookDelivery, WebhookEndpoint
from app.webhook_worker import request_for


async def test_device_options_are_passive(client):
    response = await client.get("/iclock/cdata", params={"SN": "TEST001", "options": "all", "FWVersion": "1.2.3"})
    assert response.status_code == 200
    assert "GET OPTION FROM: TEST001" in response.text
    assert "Realtime=1" in response.text
    assert "CLEAR" not in response.text


async def test_punch_ingestion_is_idempotent(client):
    line = "42\t2026-10-26 09:31:20\t0\t1\t\t0"
    for _ in range(2):
        response = await client.post("/iclock/cdata?SN=TEST001&table=ATTLOG&Stamp=99", content=line)
        assert response.status_code == 200
    async with SessionLocal() as db:
        assert await db.scalar(select(func.count()).select_from(Punch)) == 1


async def test_device_originated_employee(client):
    body = "PIN=42 Name=Test User Pri=0 Passwd= Card=12345"
    response = await client.post("/iclock/cdata.php?SN=TEST001&table=USER", content=body)
    assert response.text == "OK: 1"
    async with SessionLocal() as db:
        from app.models import Device
        device = await db.scalar(select(Device).where(Device.serial_number == "TEST001"))
        employee = await db.scalar(select(Employee).where(Employee.device_id == device.id, Employee.employee_code == "42"))
        assert employee.name == "Test User"
        assert employee.card_number == "12345"


async def test_same_employee_id_is_independent_per_device(client):
    await client.post("/iclock/cdata?SN=OFFICE-A&table=USER", content="PIN=1 Name=Alice Card=100")
    await client.post("/iclock/cdata?SN=OFFICE-B&table=USER", content="PIN=1 Name=Bob Card=200")
    async with SessionLocal() as db:
        employees = (await db.scalars(select(Employee).where(Employee.employee_code == "1").order_by(Employee.name))).all()
        assert len(employees) == 2
        assert [(employee.name, employee.card_number) for employee in employees] == [("Alice", "100"), ("Bob", "200")]
        assert employees[0].device_id != employees[1].device_id


async def test_users_and_fingerprints_inside_operlog_are_imported(client):
    body = "\n".join(
        [
            "USER PIN=51\tName=Office User\tPri=0\tPasswd=\tCard=9876\tGrp=1",
            "FP PIN=51\tFID=3\tSize=12\tValid=1\tTMP=template-data",
        ]
    )
    response = await client.post("/iclock/cdata.aspx?SN=OPERLOG01&table=OPERLOG&OpStamp=10", content=body)
    assert response.text == "OK: 2"
    async with SessionLocal() as db:
        from app.models import Biometric, Device
        device = await db.scalar(select(Device).where(Device.serial_number == "OPERLOG01"))
        employee = await db.scalar(select(Employee).where(Employee.device_id == device.id, Employee.employee_code == "51"))
        assert employee.name == "Office User"
        assert employee.card_number == "9876"
        biometric = await db.scalar(select(Biometric).where(Biometric.employee_id == employee.id, Biometric.slot == "3"))
        assert biometric.kind == "fingerprint"
        assert biometric.template == "template-data"


async def test_command_handshake(client):
    await client.get("/iclock/ping?SN=TEST001")
    async with SessionLocal() as db:
        from app.models import Device
        device = await db.scalar(select(Device).where(Device.serial_number == "TEST001"))
        db.add(DeviceCommand(device_id=device.id, title="Check", command="CHECK"))
        await db.commit()
    response = await client.get("/iclock/getrequest.aspx?SN=TEST001")
    assert response.text.startswith("C:1:CHECK")
    response = await client.post("/iclock/devicecmd?SN=TEST001", content="ID=1&Return=0&CMD=CHECK")
    assert response.text == "OK"
    async with SessionLocal() as db:
        command = await db.get(DeviceCommand, 1)
        assert command.status == "completed"


async def test_admin_requires_auth(client):
    response = await client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
    login = await client.post("/admin/login", data={"username": "test", "password": "test"}, follow_redirects=False)
    assert login.status_code == 303
    assert (await client.get("/admin")).status_code == 200
    await client.get("/admin/logout", follow_redirects=False)
    assert (await client.get("/admin", follow_redirects=False)).status_code == 303


async def test_employee_page_filters_by_device(client):
    await client.post("/iclock/cdata?SN=FILTER-A&table=USER", content="PIN=ANY-ID Name=Alice")
    await client.post("/iclock/cdata?SN=FILTER-B&table=USER", content="PIN=ANY-ID Name=Bob")
    async with SessionLocal() as db:
        from app.models import Device
        device = await db.scalar(select(Device).where(Device.serial_number == "FILTER-A"))
        device_id = device.id
    await client.post("/admin/login", data={"username": "test", "password": "test"})
    response = await client.get(
        "/admin/employees",
        params={"device_id": device_id},
    )
    assert response.status_code == 200
    assert "Alice" in response.text
    assert "Bob" not in response.text
    assert "1–20" not in response.text
    assert "<th>Card</th>" not in response.text
    assert "<th>Privilege</th>" not in response.text


async def test_device_name_can_be_changed(client):
    await client.get("/iclock/ping?SN=OFFICE-NAME")
    async with SessionLocal() as db:
        from app.models import Device
        device = await db.scalar(select(Device).where(Device.serial_number == "OFFICE-NAME"))
        device_id = device.id
    await client.post("/admin/login", data={"username": "test", "password": "test"})
    response = await client.post(
        f"/admin/devices/{device_id}/name",
        data={"name": "Abhyuday"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    async with SessionLocal() as db:
        from app.models import Device
        assert (await db.get(Device, device_id)).name == "Abhyuday"


async def test_webhook_scope_queues_only_global_and_matching_device(client):
    await client.get("/iclock/ping?SN=OFFICE-A")
    await client.get("/iclock/ping?SN=OFFICE-B")
    async with SessionLocal() as db:
        from app.models import Device
        a = await db.scalar(select(Device).where(Device.serial_number == "OFFICE-A"))
        b = await db.scalar(select(Device).where(Device.serial_number == "OFFICE-B"))
        db.add_all(
            [
                WebhookEndpoint(name="Global", url="https://example.com/global", format="normalized"),
                WebhookEndpoint(name="A only", url="https://example.com/a", format="frappe_attendance", device_id=a.id),
                WebhookEndpoint(name="B only", url="https://example.com/b", format="frappe_attendance", device_id=b.id),
            ]
        )
        await db.commit()
    await client.post("/iclock/cdata?SN=OFFICE-A&table=ATTLOG", content="51\t2026-10-28 12:06:51\t0\t1\t\t0")
    async with SessionLocal() as db:
        deliveries = (await db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.endpoint_id))).all()
        assert len(deliveries) == 2
        endpoint_names = [await db.scalar(select(WebhookEndpoint.name).where(WebhookEndpoint.id == item.endpoint_id)) for item in deliveries]
        assert endpoint_names == ["Global", "A only"]


def test_frappe_request_matches_whitelisted_function_arguments():
    from app.models import Device
    device = Device(id=1, serial_number="SERIAL1", name="Abhyuday")
    punch = Punch(
        id=1,
        event_id="event-1",
        device_id=1,
        employee_code="51",
        punch_time=datetime(2026, 10, 28, 6, 36, 51, tzinfo=timezone.utc),
        status="0",
        verify="1",
        work_code="",
        received_at=datetime(2026, 10, 28, 7, 42, 42, tzinfo=timezone.utc),
        raw_data="51\\t2026-10-28 12:06:51\\t0\\t1",
    )
    punch.device = device
    endpoint = WebhookEndpoint(
        id=1,
        name="Frappe",
        url="https://example.com/api/method/test",
        format="frappe_attendance",
        bearer_token="token-value",
        secret="signing-secret",
    )
    delivery = WebhookDelivery(id=1, endpoint_id=1, punch_id=1)
    delivery.endpoint = endpoint
    delivery.punch = punch
    body, headers = request_for(delivery, get_settings())
    payload = json.loads(body)
    assert payload["device_employee_id"] == "51"
    data = json.loads(payload["data"])
    assert data[0]["attendance_from_device"] == "2026-10-28 12:06:51"
    assert data[0]["json"]["device_name"] == "Abhyuday"
    assert headers["Authorization"] == "Bearer token-value"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-SMRI-Signature"].startswith("sha256=")

from datetime import datetime
from urllib.parse import parse_qsl
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .database import get_db
from .models import Biometric, Device, DeviceCommand, Employee, OperationLog, Punch, WebhookDelivery, WebhookEndpoint
from .protocol import now_utc, option_response, parse_attlog_line, parse_device_datetime, parse_key_values, parse_user_line

router = APIRouter()


async def get_or_create_device(request: Request, db: AsyncSession, serial: str) -> Device:
    serial = serial.strip() or "UNKNOWN"
    device = await db.scalar(select(Device).where(Device.serial_number == serial))
    now = now_utc()
    if device is None:
        device = Device(serial_number=serial, name=serial, first_seen=now)
        db.add(device)
        await db.flush()
    device.last_ping = now
    device.ip_address = request.client.host if request.client else None
    return device


async def queue_deliveries(db: AsyncSession, punch: Punch) -> None:
    endpoints = (
        await db.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.enabled.is_(True),
                or_(WebhookEndpoint.device_id.is_(None), WebhookEndpoint.device_id == punch.device_id),
            )
        )
    ).all()
    for endpoint in endpoints:
        db.add(WebhookDelivery(endpoint_id=endpoint.id, punch_id=punch.id, next_attempt_at=now_utc()))


async def upsert_employee(db: AsyncSession, device: Device, line: str) -> bool:
    parsed = parse_user_line(line)
    if not parsed:
        return False
    employee = await db.scalar(
        select(Employee).where(Employee.device_id == device.id, Employee.employee_code == parsed["employee_code"])
    )
    if employee is None:
        employee = Employee(employee_code=parsed["employee_code"], device_id=device.id)
        db.add(employee)
    for field in ("name", "card_number", "privilege", "password", "raw_data"):
        if parsed.get(field) is not None:
            setattr(employee, field, parsed[field])
    employee.updated_at = now_utc()
    return True


async def save_biometric(db: AsyncSession, device: Device, line: str, kind: str) -> bool:
    values = parse_key_values(line)
    code = values.get("PIN") or values.get("UID")
    if not code:
        return False
    employee = await db.scalar(select(Employee).where(Employee.device_id == device.id, Employee.employee_code == code))
    if employee is None:
        employee = Employee(employee_code=code, device_id=device.id, updated_at=now_utc())
        db.add(employee)
        await db.flush()
    slot = values.get("FID") or values.get("FINGERID") or values.get("FACEID") or "0"
    biometric = await db.scalar(
        select(Biometric).where(
            Biometric.employee_id == employee.id,
            Biometric.device_id == device.id,
            Biometric.kind == kind,
            Biometric.slot == slot,
        )
    )
    if biometric is None:
        biometric = Biometric(employee_id=employee.id, device_id=device.id, kind=kind, slot=slot)
        db.add(biometric)
    biometric.template = values.get("TMP") or values.get("TEMPLATE") or values.get("CONTENT")
    biometric.raw_data = line
    biometric.received_at = now_utc()
    return True


@router.api_route("/iclock/cdata", methods=["GET", "POST"], response_class=PlainTextResponse)
@router.api_route("/iclock/cdata.aspx", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
@router.api_route("/iclock/cdata.php", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
async def cdata(request: Request, db: AsyncSession = Depends(get_db), settings: Settings = Depends(get_settings)):
    query = request.query_params
    serial = query.get("SN", query.get("sn", "UNKNOWN"))
    device = await get_or_create_device(request, db, serial)
    if request.method == "GET":
        device.raw_options = str(dict(query))
        device.device_type = query.get("DeviceType") or query.get("~DeviceName") or device.device_type
        device.firmware = query.get("FWVersion") or query.get("FirmVer") or device.firmware
        await db.commit()
        return option_response(serial, device.transaction_stamp, device.operation_stamp, settings.device_error_delay, settings.device_trans_interval)

    table = query.get("table", "ATTLOG").upper()
    stamp = query.get("Stamp") or query.get("stamp")
    body = (await request.body()).decode("utf-8", errors="replace").lstrip("\ufeff")
    count = 0
    for line in filter(None, (item.strip() for item in body.splitlines())):
        if table == "ATTLOG":
            parsed = parse_attlog_line(line, settings.tz)
            if not parsed:
                continue
            employee = await db.scalar(
                select(Employee).where(Employee.device_id == device.id, Employee.employee_code == parsed["employee_code"])
            )
            if employee is None:
                db.add(Employee(employee_code=parsed["employee_code"], device_id=device.id, updated_at=now_utc(), raw_data="Created from punch"))
            punch = Punch(event_id=str(uuid4()), device_id=device.id, received_at=now_utc(), **parsed)
            try:
                async with db.begin_nested():
                    db.add(punch)
                    await db.flush()
                await queue_deliveries(db, punch)
                count += 1
            except IntegrityError:
                pass
        elif table in {"USER", "USERINFO"}:
            count += int(await upsert_employee(db, device, line))
        elif table in {"FP", "FINGERPRINT"}:
            count += int(await save_biometric(db, device, line, "fingerprint"))
        elif table in {"FACE", "FACEINFO", "BIODATA"}:
            count += int(await save_biometric(db, device, line, "face"))
        elif table == "OPERLOG":
            record_type = line.split(None, 1)[0].upper() if line.split(None, 1) else ""
            if record_type in {"USER", "USERINFO"}:
                await upsert_employee(db, device, line)
            elif record_type in {"FP", "FINGERPRINT"}:
                await save_biometric(db, device, line, "fingerprint")
            elif record_type in {"FACE", "FACEINFO", "BIODATA"}:
                await save_biometric(db, device, line, "face")
            fields = line.split("\t")
            occurred = next((parse_device_datetime(value, settings.tz) for value in fields if parse_device_datetime(value, settings.tz)), None)
            db.add(OperationLog(device_id=device.id, occurred_at=occurred, operation=fields[0] if fields else record_type, raw_data=line, received_at=now_utc()))
            count += 1
    if table == "ATTLOG" and stamp:
        device.transaction_stamp = stamp
    elif table == "OPERLOG" and stamp:
        device.operation_stamp = stamp
    await db.commit()
    return f"OK: {count}"


@router.api_route("/iclock/getrequest", methods=["GET", "POST"], response_class=PlainTextResponse)
@router.api_route("/iclock/getrequest.aspx", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
@router.api_route("/iclock/getrequest.php", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
async def getrequest(request: Request, db: AsyncSession = Depends(get_db)):
    serial = request.query_params.get("SN", request.query_params.get("sn", "UNKNOWN"))
    device = await get_or_create_device(request, db, serial)
    command = await db.scalar(
        select(DeviceCommand).where(DeviceCommand.device_id == device.id, DeviceCommand.status == "pending").order_by(DeviceCommand.id).limit(1)
    )
    if command is None:
        await db.commit()
        return "OK"
    command.status = "sent"
    command.sent_at = now_utc()
    await db.commit()
    return f"C:{command.id}:{command.command}"


@router.api_route("/iclock/devicecmd", methods=["GET", "POST"], response_class=PlainTextResponse)
@router.api_route("/iclock/devicecmd.aspx", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
@router.api_route("/iclock/devicecmd.php", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
async def devicecmd(request: Request, db: AsyncSession = Depends(get_db)):
    serial = request.query_params.get("SN", request.query_params.get("sn", "UNKNOWN"))
    device = await get_or_create_device(request, db, serial)
    body = (await request.body()).decode("utf-8", errors="replace")
    values = {key.upper(): value for key, value in request.query_params.items()}
    values.update(parse_key_values(body))
    values.update({key.upper(): value for key, value in parse_qsl(body, keep_blank_values=True)})
    command_id = values.get("ID")
    if command_id and command_id.isdigit():
        command = await db.get(DeviceCommand, int(command_id))
        if command:
            command.return_code = values.get("RETURN") or values.get("RET")
            command.response = body or str(dict(request.query_params))
            command.status = "completed" if command.return_code in {None, "0", "1"} else "failed"
            command.completed_at = now_utc()
    db.add(OperationLog(device_id=device.id, operation="COMMAND_RESPONSE", raw_data=body or str(dict(request.query_params)), received_at=now_utc()))
    await db.commit()
    return "OK"


@router.api_route("/iclock/ping", methods=["GET", "POST"], response_class=PlainTextResponse)
@router.api_route("/iclock/ping.aspx", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
@router.api_route("/iclock/ping.php", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
async def ping(request: Request, db: AsyncSession = Depends(get_db)):
    serial = request.query_params.get("SN", request.query_params.get("sn", "UNKNOWN"))
    await get_or_create_device(request, db, serial)
    await db.commit()
    return "OK"


@router.api_route("/iclock/fdata", methods=["GET", "POST"], response_class=PlainTextResponse)
@router.api_route("/iclock/fdata.aspx", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
@router.api_route("/iclock/fdata.php", methods=["GET", "POST"], response_class=PlainTextResponse, include_in_schema=False)
async def fdata(request: Request, db: AsyncSession = Depends(get_db)):
    serial = request.query_params.get("SN", request.query_params.get("sn", "UNKNOWN"))
    device = await get_or_create_device(request, db, serial)
    body = (await request.body()).decode("utf-8", errors="replace")
    db.add(OperationLog(device_id=device.id, operation="FDATA", raw_data=body, received_at=now_utc()))
    await db.commit()
    return "OK"

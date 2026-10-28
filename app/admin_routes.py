from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from .auth import valid_credentials
from .config import Settings, get_settings
from .database import get_db
from .models import Device, DeviceCommand, Employee, Punch, WebhookDelivery, WebhookEndpoint
from .protocol import now_utc

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def local_datetime(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(get_settings().tz).strftime("%Y-%m-%d %I:%M:%S %p IST")


templates.env.filters["local_datetime"] = local_datetime


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("admin_authenticated"):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    settings: Settings = Depends(get_settings),
):
    if not valid_credentials(username, password, settings):
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"}, status_code=401)
    request.session.clear()
    request.session["admin_authenticated"] = True
    request.session["admin_username"] = username
    return RedirectResponse("/admin", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@router.get("")
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    devices = (await db.scalars(select(Device).order_by(Device.name))).all()
    recent = (await db.scalars(select(Punch).options(joinedload(Punch.device)).order_by(Punch.punch_time.desc()).limit(20))).all()
    counts = {
        "devices": await db.scalar(select(func.count()).select_from(Device)),
        "employees": await db.scalar(select(func.count()).select_from(Employee)),
        "punches": await db.scalar(select(func.count()).select_from(Punch)),
        "pending": await db.scalar(select(func.count()).select_from(WebhookDelivery).where(WebhookDelivery.status == "pending")),
    }
    def is_online(value):
        if value is None:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return (now_utc() - value).total_seconds() < 300

    return templates.TemplateResponse(request, "dashboard.html", {"devices": devices, "recent": recent, "counts": counts, "is_online": is_online})


@router.get("/punches")
async def punches(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    statement = select(Punch).order_by(Punch.punch_time.desc())
    if q:
        statement = statement.where(or_(Punch.employee_code.ilike(f"%{q}%"), Punch.raw_data.ilike(f"%{q}%")))
    rows = (await db.scalars(statement.offset((page - 1) * 100).limit(100))).all()
    devices = {item.id: item for item in (await db.scalars(select(Device))).all()}
    return templates.TemplateResponse(request, "punches.html", {"punches": rows, "devices": devices, "q": q, "page": page})


@router.get("/punches.csv")
async def punches_csv(db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Punch).order_by(Punch.punch_time.desc()))).all()

    async def content():
        yield "event_id,device_id,employee_code,punch_time,status,verify,work_code,received_at\n"
        for row in rows:
            values = [row.event_id, row.device_id, row.employee_code, row.punch_time.isoformat(), row.status, row.verify, row.work_code, row.received_at.isoformat()]
            yield ",".join('"' + str(value).replace('"', '""') + '"' for value in values) + "\n"

    return StreamingResponse(content(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=punches.csv"})


@router.get("/employees")
async def employees(
    request: Request,
    device_id: int | None = Query(None, ge=1),
    q: str = "",
    db: AsyncSession = Depends(get_db),
):
    statement = select(Employee)
    if device_id is not None:
        statement = statement.where(Employee.device_id == device_id)
    if q:
        statement = statement.where(
            or_(Employee.employee_code.ilike(f"%{q}%"), Employee.name.ilike(f"%{q}%"))
        )
    rows = (await db.scalars(statement.order_by(Employee.device_id, Employee.employee_code))).all()
    device_rows = (await db.scalars(select(Device).order_by(Device.name))).all()
    devices = {item.id: item for item in device_rows}
    return templates.TemplateResponse(
        request,
        "employees.html",
        {"employees": rows, "devices": devices, "device_rows": device_rows, "selected_device_id": device_id, "q": q},
    )


SAFE_COMMANDS = {
    "device_info": ("Request device information", "INFO"),
    "check": ("Check device", "CHECK"),
    "fetch_users": ("Request users from device", "DATA QUERY USERINFO"),
    "fetch_punches": ("Request attendance records", "DATA QUERY ATTLOG"),
    "fetch_fingerprints": ("Request fingerprint templates", "DATA QUERY FINGERTMP"),
}


@router.get("/devices")
async def devices(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(Device).order_by(Device.name))).all()
    commands = (await db.scalars(select(DeviceCommand).order_by(DeviceCommand.created_at.desc()).limit(100))).all()
    return templates.TemplateResponse(request, "devices.html", {"devices": rows, "commands": commands, "presets": SAFE_COMMANDS})


@router.post("/devices/{device_id}/name")
async def update_device_name(device_id: int, name: str = Form(...), db: AsyncSession = Depends(get_db)):
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    clean_name = name.strip()
    if not clean_name or len(clean_name) > 255:
        raise HTTPException(400, "Device name must be between 1 and 255 characters")
    device.name = clean_name
    await db.commit()
    return RedirectResponse("/admin/devices", status_code=303)


@router.post("/devices/{device_id}/command")
async def create_command(
    device_id: int,
    preset: str = Form(...),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    device = await db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    if preset == "set_time":
        local_time = datetime.now(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
        title, command = "Set device time", f"SET OPTIONS DateTime={local_time}"
    elif preset in SAFE_COMMANDS:
        title, command = SAFE_COMMANDS[preset]
    else:
        raise HTTPException(400, "Unsupported command")
    db.add(DeviceCommand(device_id=device.id, title=title, command=command, safe=True))
    await db.commit()
    return RedirectResponse("/admin/devices", status_code=303)


@router.get("/webhooks")
async def webhooks(request: Request, db: AsyncSession = Depends(get_db)):
    endpoints = (await db.scalars(select(WebhookEndpoint).order_by(WebhookEndpoint.name))).all()
    deliveries = (await db.scalars(select(WebhookDelivery).order_by(WebhookDelivery.id.desc()).limit(100))).all()
    device_rows = (await db.scalars(select(Device).order_by(Device.name))).all()
    devices = {device.id: device for device in device_rows}
    return templates.TemplateResponse(
        request,
        "webhooks.html",
        {"endpoints": endpoints, "deliveries": deliveries, "device_rows": device_rows, "devices": devices},
    )


@router.post("/webhooks")
async def add_webhook(
    name: str = Form(...),
    url: str = Form(...),
    device_id: str = Form(""),
    format: str = Form("normalized"),
    secret: str = Form(""),
    bearer_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Webhook URL must use HTTP or HTTPS")
    if format not in {"normalized", "frappe_attendance"}:
        raise HTTPException(400, "Unsupported webhook format")
    scoped_device_id = int(device_id) if device_id else None
    if scoped_device_id is not None and not await db.get(Device, scoped_device_id):
        raise HTTPException(400, "Selected device does not exist")
    db.add(
        WebhookEndpoint(
            name=name.strip(),
            url=url.strip(),
            device_id=scoped_device_id,
            format=format,
            secret=secret or None,
            bearer_token=bearer_token or None,
        )
    )
    await db.commit()
    return RedirectResponse("/admin/webhooks", status_code=303)


@router.post("/webhooks/{endpoint_id}/toggle")
async def toggle_webhook(endpoint_id: int, db: AsyncSession = Depends(get_db)):
    endpoint = await db.get(WebhookEndpoint, endpoint_id)
    if not endpoint:
        raise HTTPException(404, "Webhook not found")
    endpoint.enabled = not endpoint.enabled
    await db.commit()
    return RedirectResponse("/admin/webhooks", status_code=303)


import asyncio
import hashlib
import hmac
import json
from datetime import timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .config import Settings
from .database import SessionLocal
from .models import Punch, WebhookDelivery
from .protocol import now_utc


def normalized_payload_for(delivery: WebhookDelivery) -> dict:
    punch = delivery.punch
    return {
        "event": "attendance.punch",
        "event_id": punch.event_id,
        "device_serial": punch.device.serial_number,
        "employee_code": punch.employee_code,
        "punch_time": punch.punch_time.isoformat(),
        "status": punch.status,
        "verification_type": punch.verify,
        "work_code": punch.work_code or None,
        "received_at": punch.received_at.isoformat(),
    }


def frappe_payload_for(delivery: WebhookDelivery, settings: Settings) -> dict[str, str]:
    punch = delivery.punch
    punch_time = punch.punch_time
    if punch_time.tzinfo is None:
        punch_time = punch_time.replace(tzinfo=timezone.utc)
    attendance_time = punch_time.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M:%S")
    log_data = {
        "event_id": punch.event_id,
        "device_serial": punch.device.serial_number,
        "device_name": punch.device.name,
        "employee_code": punch.employee_code,
        "status": punch.status,
        "verification_type": punch.verify,
        "work_code": punch.work_code or None,
        "raw": punch.raw_data,
    }
    return {
        "device_employee_id": punch.employee_code,
        "data": json.dumps([{"json": log_data, "attendance_from_device": attendance_time}], separators=(",", ":")),
    }


def request_for(delivery: WebhookDelivery, settings: Settings) -> tuple[bytes, dict[str, str]]:
    endpoint = delivery.endpoint
    if endpoint.format == "frappe_attendance":
        body = json.dumps(frappe_payload_for(delivery, settings), separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json", "X-SMRI-Event-ID": delivery.punch.event_id}
    else:
        payload = normalized_payload_for(delivery)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        headers = {"Content-Type": "application/json", "X-SMRI-Event-ID": payload["event_id"]}
    if endpoint.bearer_token:
        headers["Authorization"] = f"Bearer {endpoint.bearer_token}"
    if endpoint.secret:
        digest = hmac.new(endpoint.secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-SMRI-Signature"] = f"sha256={digest}"
    return body, headers


async def deliver_batch(settings: Settings) -> int:
    async with SessionLocal() as db:
        statement = (
            select(WebhookDelivery)
            .options(selectinload(WebhookDelivery.endpoint), selectinload(WebhookDelivery.punch).selectinload(Punch.device))
            .where(
                WebhookDelivery.status == "pending",
                WebhookDelivery.next_attempt_at <= now_utc(),
            )
            .order_by(WebhookDelivery.id)
            .limit(50)
        )
        deliveries = (await db.scalars(statement)).all()
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            for delivery in deliveries:
                if not delivery.endpoint.enabled:
                    continue
                body, headers = request_for(delivery, settings)
                delivery.attempts += 1
                try:
                    response = await client.post(delivery.endpoint.url, content=body, headers=headers)
                    delivery.response_code = response.status_code
                    if 200 <= response.status_code < 300:
                        delivery.status = "delivered"
                        delivery.delivered_at = now_utc()
                        delivery.last_error = None
                    else:
                        delivery.last_error = f"HTTP {response.status_code}: {response.text[:500]}"
                except Exception as exc:
                    delivery.last_error = str(exc)[:1000]
                if delivery.status == "pending":
                    delay = min(3600, 2 ** min(delivery.attempts, 11))
                    delivery.next_attempt_at = now_utc() + timedelta(seconds=delay)
            await db.commit()
        return len(deliveries)


async def run_webhook_worker(settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await deliver_batch(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.webhook_poll_seconds)
        except asyncio.TimeoutError:
            continue

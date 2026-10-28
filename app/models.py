from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    serial_number: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    device_type: Mapped[str | None] = mapped_column(String(100))
    firmware: Mapped[str | None] = mapped_column(String(100))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    timezone_minutes: Mapped[int | None] = mapped_column(Integer)
    transaction_stamp: Mapped[str] = mapped_column(String(64), default="0")
    operation_stamp: Mapped[str] = mapped_column(String(64), default="0")
    last_ping: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    raw_options: Mapped[str | None] = mapped_column(Text)


class Employee(Base):
    __tablename__ = "device_employees"
    __table_args__ = (
        UniqueConstraint("device_id", "employee_code", name="uq_device_employee_code"),
        Index("ix_device_employee_code", "device_id", "employee_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    employee_code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    card_number: Mapped[str | None] = mapped_column(String(100))
    privilege: Mapped[str | None] = mapped_column(String(20))
    password: Mapped[str | None] = mapped_column(String(100))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    raw_data: Mapped[str | None] = mapped_column(Text)


class Biometric(Base):
    __tablename__ = "employee_biometrics"
    __table_args__ = (UniqueConstraint("employee_id", "device_id", "kind", "slot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("device_employees.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    slot: Mapped[str] = mapped_column(String(30), default="0")
    template: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Punch(Base):
    __tablename__ = "punches"
    __table_args__ = (
        UniqueConstraint("device_id", "employee_code", "punch_time", "status", "verify", "work_code", name="uq_punch_event"),
        Index("ix_punches_time_code", "punch_time", "employee_code"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    employee_code: Mapped[str] = mapped_column(String(100), index=True)
    punch_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="0")
    verify: Mapped[str] = mapped_column(String(20), default="0")
    work_code: Mapped[str] = mapped_column(String(50), default="")
    reserved: Mapped[str | None] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    raw_data: Mapped[str] = mapped_column(Text)
    device: Mapped[Device] = relationship()


class OperationLog(Base):
    __tablename__ = "operation_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation: Mapped[str | None] = mapped_column(String(50))
    raw_data: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class DeviceCommand(Base):
    __tablename__ = "device_commands"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    command: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    safe: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    return_code: Mapped[str | None] = mapped_column(String(50))
    response: Mapped[str | None] = mapped_column(Text)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(1000))
    secret: Mapped[str | None] = mapped_column(String(255))
    bearer_token: Mapped[str | None] = mapped_column(String(1000))
    format: Mapped[str] = mapped_column(String(30), default="normalized")
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    device: Mapped[Device | None] = relationship()


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("endpoint_id", "punch_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    punch_id: Mapped[int] = mapped_column(ForeignKey("punches.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    response_code: Mapped[int | None] = mapped_column(Integer)
    endpoint: Mapped[WebhookEndpoint] = relationship()
    punch: Mapped[Punch] = relationship()

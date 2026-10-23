"""Database tables.

Only hashes of access tokens are stored. An outage keeps its plan, milestones and the battery
levels the plan was computed from as JSON, because they are snapshots that change together.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.catalog import BackupType, DeviceIcon, DeviceKind
from app.schemas import OutageSource, OutageStatus


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as naive UTC in SQLite and always returns them timezone-aware."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert to naive UTC before writing."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not allowed; use timeutil.utcnow()")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach UTC when reading."""
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    """Base class for all tables."""

    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UTCDateTime(),
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class Household(Base):
    """A home with a patient. Its devices, backups and caregivers load with it."""

    __tablename__ = "households"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    patient_first_name: Mapped[str] = mapped_column(String(60))
    address: Mapped[str | None] = mapped_column(String(300))
    lat: Mapped[float | None]
    lon: Mapped[float | None]
    state: Mapped[str | None] = mapped_column(String(2))
    utility_name: Mapped[str | None] = mapped_column(String(100))
    medical_program_registered: Mapped[bool] = mapped_column(default=False)
    join_code: Mapped[str] = mapped_column(String(6), unique=True)
    armed: Mapped[bool] = mapped_column(default=True)
    owner_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime]

    devices: Mapped[list["Device"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="Device.created_at",
    )
    backups: Mapped[list["Backup"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="Backup.created_at",
    )
    caregivers: Mapped[list["Caregiver"]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="Caregiver.joined_at",
    )


class Device(Base):
    """A medical device in a household."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    household_id: Mapped[str] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[DeviceKind] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    icon: Mapped[DeviceIcon] = mapped_column(String(30))
    life_sustaining: Mapped[bool]
    draw_watts: Mapped[float]
    saver_draw_watts: Mapped[float | None]
    saver_enabled: Mapped[bool] = mapped_column(default=False)
    duty_cycle: Mapped[float]
    ac_powered: Mapped[bool]
    internal_battery_wh: Mapped[float]
    spare_batteries_wh: Mapped[float] = mapped_column(default=0.0)
    charge_percent: Mapped[float] = mapped_column(default=100.0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime]


class Backup(Base):
    """A shared backup power source."""

    __tablename__ = "backups"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    household_id: Mapped[str] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[BackupType] = mapped_column(String(20))
    label: Mapped[str] = mapped_column(String(80))
    capacity_wh: Mapped[float | None]
    max_output_watts: Mapped[float]
    has_ac_outlet: Mapped[bool]
    charge_percent: Mapped[float | None]
    fuel_hours: Mapped[float | None]
    created_at: Mapped[datetime]


class Caregiver(Base):
    """Someone who receives alerts for a household (read-only access plus acknowledgements)."""

    __tablename__ = "caregivers"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    household_id: Mapped[str] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    relationship: Mapped[str | None] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    joined_at: Mapped[datetime]


class Outage(Base):
    """An outage or drill. At most one per household is active (enforced by a partial index)."""

    __tablename__ = "outages"
    __table_args__ = (
        Index(
            "one_active_outage_per_household",
            "household_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    household_id: Mapped[str] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[OutageStatus] = mapped_column(String(10))
    source: Mapped[OutageSource] = mapped_column(String(20))
    speed: Mapped[float] = mapped_column(default=1.0)
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    last_check_in_at: Mapped[datetime]
    last_check_in_ok: Mapped[bool] = mapped_column(default=True)
    missed_check_in_alerts: Mapped[int] = mapped_column(default=0)
    """Missed-check-in alerts sent since the last check-in."""
    help_site_name: Mapped[str | None] = mapped_column(String(200))
    travel_minutes: Mapped[float]
    plan: Mapped[dict[str, Any]]
    milestones: Mapped[list[Any]]
    energy_state: Mapped[dict[str, Any]]
    """Device and backup levels the current plan was computed from (planner inputs as JSON)."""
    version: Mapped[int] = mapped_column(Integer, default=1)

    acks: Mapped[list["Ack"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="selectin", order_by="Ack.at"
    )

    @property
    def drill(self) -> bool:
        """True for practice runs (the API's convenience flag)."""
        return self.source == "drill"

    # Optimistic locking: the ticker and a check-in can't silently overwrite each other.
    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class Ack(Base):
    """A caregiver's acknowledgement of an outage. Name is copied so history survives removal."""

    __tablename__ = "acks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    outage_id: Mapped[str] = mapped_column(ForeignKey("outages.id", ondelete="CASCADE"), index=True)
    caregiver_id: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(60))
    message: Mapped[str | None] = mapped_column(String(280))
    eta_minutes: Mapped[float | None]
    at: Mapped[datetime]

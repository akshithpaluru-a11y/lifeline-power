"""Devices and backup power: filling in catalog defaults and keeping the figures consistent.

These functions only build or change ORM objects; the household service saves them.
"""

from datetime import datetime

from app import models
from app.catalog import FUEL_BACKUP_TYPES, backup_spec, device_spec
from app.errors import bad_request
from app.ids import new_id
from app.schemas import BackupCreate, BackupUpdate, DeviceCreate, DeviceUpdate

_BATTERY_ONLY_FIELDS = ("capacity_wh", "charge_percent")
_FUEL_ONLY_FIELDS = ("fuel_hours",)


def _pick[T](value: T | None, default: T) -> T:
    """The client's value if they sent one, else the default (keeps 0 and False)."""
    return default if value is None else value


# --- Devices -----------------------------------------------------------------------------


def new_device(body: DeviceCreate, now: datetime) -> models.Device:
    """A device of `body.kind`, with every field the client left out taken from the catalog."""
    spec = device_spec(body.kind)
    saver_sent = "saver_draw_watts" in body.model_fields_set
    device = models.Device(
        id=new_id("dv"),
        kind=body.kind,
        label=body.label or spec.name,
        icon=_pick(body.icon, spec.icon),
        life_sustaining=_pick(body.life_sustaining, spec.life_sustaining_default),
        draw_watts=_pick(body.draw_watts, spec.typical_draw_watts),
        saver_draw_watts=body.saver_draw_watts if saver_sent else spec.saver_draw_watts,
        saver_enabled=_pick(body.saver_enabled, False),
        duty_cycle=_pick(body.duty_cycle, spec.duty_cycle),
        ac_powered=_pick(body.ac_powered, spec.ac_powered),
        internal_battery_wh=_pick(body.internal_battery_wh, spec.internal_battery_wh),
        spare_batteries_wh=_pick(body.spare_batteries_wh, 0.0),
        charge_percent=_pick(body.charge_percent, 100.0),
        notes=body.notes or "",
        created_at=now,
    )
    _check_saver(device, sent=saver_sent and body.saver_draw_watts is not None)
    return device


def update_device(device: models.Device, body: DeviceUpdate) -> None:
    """Apply the fields the client sent (`null` clears `saver_draw_watts` and `notes`)."""
    changes = body.model_dump(exclude_unset=True)
    if "notes" in changes:
        changes["notes"] = changes["notes"] or ""
    for name, value in changes.items():
        setattr(device, name, value)
    _check_saver(device, sent=changes.get("saver_draw_watts") is not None)


def _check_saver(device: models.Device, *, sent: bool) -> None:
    """A saver setting must use less power than normal.

    If the client typed one that doesn't, that's an error. If it came from the catalog (or was
    set earlier) and the client has since entered a lower normal draw, it no longer fits this
    device, so it is dropped.
    """
    if device.saver_draw_watts is None or device.saver_draw_watts < device.draw_watts:
        return
    if sent:
        raise bad_request("saver_draw_watts must be lower than draw_watts.")
    device.saver_draw_watts = None


# --- Backups -----------------------------------------------------------------------------


def new_backup(body: BackupCreate, now: datetime) -> models.Backup:
    """A backup of `body.type`, with every field the client left out taken from the catalog."""
    spec = backup_spec(body.type)
    backup = models.Backup(
        id=new_id("bk"),
        type=body.type,
        label=body.label or spec.name,
        capacity_wh=body.capacity_wh,
        max_output_watts=_pick(body.max_output_watts, spec.typical_max_output_watts),
        has_ac_outlet=_pick(body.has_ac_outlet, spec.has_ac_outlet),
        charge_percent=body.charge_percent,
        fuel_hours=body.fuel_hours,
        created_at=now,
    )
    _fit_to_type(backup)
    return backup


def update_backup(backup: models.Backup, body: BackupUpdate) -> None:
    """Apply the fields the client sent.

    Changing `type` resets every figure the client didn't send (and a label that was just the
    old type's name) to the new type's typical values, because a UPS's output limit says nothing
    about a generator's.
    """
    changes = body.model_dump(exclude_unset=True)
    new_type = changes.get("type", backup.type)
    if new_type != backup.type:
        old_spec, spec = backup_spec(backup.type), backup_spec(new_type)
        if "label" not in changes and backup.label == old_spec.name:
            backup.label = spec.name
        defaults = {
            "max_output_watts": spec.typical_max_output_watts,
            "has_ac_outlet": spec.has_ac_outlet,
            "capacity_wh": None,
            "charge_percent": None,
            "fuel_hours": None,
        }
        for name, value in defaults.items():
            setattr(backup, name, value)
    for name, value in changes.items():
        setattr(backup, name, value)
    _fit_to_type(backup)


def _fit_to_type(backup: models.Backup) -> None:
    """Check the figures suit the backup's type and fill in the ones it needs.

    Batteries (power stations, UPS units) have a capacity and a charge level; generators and
    car inverters have fuel hours instead. A missing figure gets the catalog's typical value
    (and charge 100%); a figure that doesn't apply to the type is an error.
    """
    spec = backup_spec(backup.type)
    if backup.type in FUEL_BACKUP_TYPES:
        if any(getattr(backup, name) is not None for name in _BATTERY_ONLY_FIELDS):
            raise bad_request(
                "Generators and car inverters run on fuel: leave capacity_wh and charge_percent "
                "empty and set fuel_hours instead."
            )
        backup.fuel_hours = _pick(backup.fuel_hours, spec.typical_fuel_hours)
    else:
        if any(getattr(backup, name) is not None for name in _FUEL_ONLY_FIELDS):
            raise bad_request("fuel_hours only applies to generators and car inverters.")
        backup.capacity_wh = _pick(backup.capacity_wh, spec.typical_capacity_wh)
        backup.charge_percent = _pick(backup.charge_percent, 100.0)

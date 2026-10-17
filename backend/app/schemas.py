"""Request and response models. Field names and shapes follow `docs/API.md` exactly.

Anything not in the contract is marked "Additive" in its description (see
`docs/CONTRACT_NOTES.md`). Request models ignore unknown fields, so a client may send back a whole
object it received.
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.catalog import BackupIcon, BackupType, DeviceCategory, DeviceIcon, DeviceKind
from app.timeutil import UtcDateTime

_STATES_AND_TERRITORIES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP"
)
US_STATE_CODES = frozenset(_STATES_AND_TERRITORIES.split())

OutageSource = Literal["charging_lost", "manual", "drill"]
OutageStatus = Literal["active", "ended"]
Severity = Literal["info", "warning", "critical"]
MilestoneKey = Literal[
    "power_lost", "conserve", "check_in", "halfway", "leave_by", "critical_low", "depleted"
]
DeviceStatus = Literal["ok", "limited", "no_backup"]
RecommendationKind = Literal[
    "saver_mode", "add_backup", "charge", "register_program", "caregiver", "location", "safety"
]
HelpSiteKind = Literal[
    "hospital", "fire_station", "library", "community_centre", "police", "shelter", "other"
]
Confidence = Literal["high", "medium", "low"]

Percent = Annotated[float, Field(ge=0, le=100)]
Watts = Annotated[float, Field(gt=0, le=10_000)]
WattHours = Annotated[float, Field(ge=0, le=100_000)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class ApiModel(BaseModel):
    """Base for response models: built from ORM objects or plain values."""

    model_config = ConfigDict(from_attributes=True)


class RequestModel(BaseModel):
    """Base for request bodies: trims strings and ignores fields we don't know."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


def _normalize_state(value: str | None) -> str | None:
    """Upper-case a 2-letter state code and check it is a real US state or territory."""
    if not value:
        return None
    code = value.upper()
    if code not in US_STATE_CODES:
        raise ValueError("must be a 2-letter US state code such as NE")
    return code


StateCode = Annotated[str | None, AfterValidator(_normalize_state)]


def _reject_nulls(model: BaseModel, nullable: frozenset[str]) -> None:
    """Raise if the client explicitly sent null for a field that can't be empty."""
    for name in model.model_fields_set - nullable:
        if getattr(model, name) is None:
            raise ValueError(f"{name} can't be null")


# --- Catalog -------------------------------------------------------------------------------


class DeviceTemplate(ApiModel):
    """A catalog entry: typical figures for one kind of device."""

    kind: DeviceKind
    name: str
    category: DeviceCategory
    icon: DeviceIcon
    life_sustaining_default: bool
    typical_draw_watts: float
    saver_draw_watts: float | None
    saver_tip: str | None
    internal_battery_wh: float
    duty_cycle: float
    ac_powered: bool
    description: str


class BackupTemplate(ApiModel):
    """A catalog entry: typical figures for one kind of backup power."""

    type: BackupType
    name: str
    icon: BackupIcon
    typical_capacity_wh: float | None
    typical_max_output_watts: float
    description: str
    has_ac_outlet: bool = Field(description="Additive: default for new backups of this type.")
    typical_fuel_hours: float | None = Field(
        description="Additive: hours per tank for generators and car inverters, else null."
    )


class DeviceCatalogResponse(BaseModel):
    """`GET /api/catalog/devices`."""

    devices: list[DeviceTemplate]


class BackupCatalogResponse(BaseModel):
    """`GET /api/catalog/backups`."""

    backups: list[BackupTemplate]


# --- Household and its parts -------------------------------------------------------------


class Device(ApiModel):
    """A medical device in a household."""

    id: str
    kind: DeviceKind
    label: str
    icon: DeviceIcon
    life_sustaining: bool
    draw_watts: float
    saver_draw_watts: float | None
    saver_enabled: bool
    duty_cycle: float
    ac_powered: bool
    internal_battery_wh: float
    spare_batteries_wh: float
    charge_percent: float
    notes: str


class BackupSource(ApiModel):
    """Shared backup power in the house."""

    id: str
    type: BackupType
    label: str
    capacity_wh: float | None
    max_output_watts: float
    has_ac_outlet: bool
    charge_percent: float | None
    fuel_hours: float | None


class Caregiver(ApiModel):
    """A family member or friend who gets alerts."""

    id: str
    name: str
    relationship: str | None
    joined_at: UtcDateTime


class Household(ApiModel):
    """A home with a patient, their devices, backup power and caregivers."""

    id: str
    name: str
    patient_first_name: str
    address: str | None
    lat: float | None
    lon: float | None
    state: str | None
    utility_name: str | None
    medical_program_registered: bool
    join_code: str
    armed: bool
    devices: list[Device]
    backups: list[BackupSource]
    caregivers: list[Caregiver]
    created_at: UtcDateTime


class HouseholdCreate(RequestModel):
    """Body of `POST /api/households`."""

    name: str = Field(min_length=1, max_length=100)
    patient_first_name: str = Field(min_length=1, max_length=60)
    address: str | None = Field(default=None, max_length=300)
    lat: Latitude | None = None
    lon: Longitude | None = None
    state: StateCode = None
    utility_name: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _lat_lon_together(self) -> "HouseholdCreate":
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class HouseholdUpdate(RequestModel):
    """Body of `PATCH /api/households/{id}`: any subset of these fields (null clears)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    patient_first_name: str | None = Field(default=None, min_length=1, max_length=60)
    address: str | None = Field(default=None, max_length=300)
    lat: Latitude | None = None
    lon: Longitude | None = None
    state: StateCode = None
    utility_name: str | None = Field(default=None, max_length=100)
    medical_program_registered: bool | None = None
    armed: bool | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "HouseholdUpdate":
        if ("lat" in self.model_fields_set) != ("lon" in self.model_fields_set):
            raise ValueError("lat and lon must be changed together")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must both be numbers or both be null")
        _reject_nulls(self, frozenset({"address", "lat", "lon", "state", "utility_name"}))
        return self


class HouseholdResponse(BaseModel):
    """`{ household }`."""

    household: Household


class HouseholdCreatedResponse(BaseModel):
    """`201 { household, owner_token }`."""

    household: Household
    owner_token: str


class JoinCodeResponse(BaseModel):
    """`{ join_code }`."""

    join_code: str


class _DeviceFields(RequestModel):
    """Device fields a client may set (all optional; unset ones keep their value)."""

    label: str | None = Field(default=None, min_length=1, max_length=80)
    icon: DeviceIcon | None = None
    life_sustaining: bool | None = None
    draw_watts: Watts | None = None
    saver_draw_watts: Watts | None = None
    saver_enabled: bool | None = None
    duty_cycle: Annotated[float, Field(gt=0, le=1)] | None = None
    ac_powered: bool | None = None
    internal_battery_wh: WattHours | None = None
    spare_batteries_wh: WattHours | None = None
    charge_percent: Percent | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check_nulls(self) -> "_DeviceFields":
        _reject_nulls(self, frozenset({"saver_draw_watts", "notes"}))
        return self


class DeviceCreate(_DeviceFields):
    """Body of `POST /api/households/{id}/devices`; unset fields come from the catalog."""

    kind: DeviceKind


class DeviceUpdate(_DeviceFields):
    """Body of `PATCH /api/households/{id}/devices/{device_id}`."""


class DeviceResponse(BaseModel):
    """`{ device }`."""

    device: Device


class _BackupFields(RequestModel):
    """Backup fields a client may set (all optional)."""

    label: str | None = Field(default=None, min_length=1, max_length=80)
    capacity_wh: Annotated[float, Field(gt=0, le=100_000)] | None = None
    max_output_watts: Watts | None = None
    has_ac_outlet: bool | None = None
    charge_percent: Percent | None = None
    fuel_hours: Annotated[float, Field(ge=0, le=500)] | None = None

    @model_validator(mode="after")
    def _check_nulls(self) -> "_BackupFields":
        _reject_nulls(self, frozenset({"capacity_wh", "charge_percent", "fuel_hours"}))
        return self


class BackupCreate(_BackupFields):
    """Body of `POST /api/households/{id}/backups`; unset fields come from the catalog."""

    type: BackupType


class BackupUpdate(_BackupFields):
    """Body of `PATCH /api/households/{id}/backups/{backup_id}` (any field except `id`)."""

    type: BackupType | None = None


class BackupResponse(BaseModel):
    """`{ backup }`."""

    backup: BackupSource


# --- Plan --------------------------------------------------------------------------------


class Allocation(ApiModel):
    """Energy a device gets from one backup, and the runtime it buys."""

    backup_id: str
    energy_wh: float
    minutes: float


class DevicePlan(ApiModel):
    """How long one device can run."""

    device_id: str
    effective_draw_watts: float
    internal_minutes: float
    total_minutes: float
    allocations: list[Allocation]
    status: DeviceStatus
    status_detail: str | None = Field(
        default=None, description="Additive: plain-English reason for `limited` / `no_backup`."
    )


class BackupPlan(ApiModel):
    """How one backup's energy is shared out."""

    backup_id: str
    usable_wh: float
    allocated_wh: float
    unused_wh: float
    fuel_minutes: float | None = Field(
        default=None,
        description="Additive: run time on the current fuel for generators / car inverters.",
    )
    device_ids: list[str] | None = Field(
        default=None,
        description="Additive: the devices the plan has plugged into this backup. Everything "
        "listed runs at once. During an outage, re-plans keep this arrangement.",
    )


class Recommendation(ApiModel):
    """Something the family can do to be safer, with the runtime it adds."""

    id: str
    kind: RecommendationKind
    title: str
    detail: str
    gain_minutes: float | None


class Plan(ApiModel):
    """What the batteries can do, computed by the planner."""

    computed_at: UtcDateTime
    assumes_saver_mode: bool
    critical_runtime_minutes: float | None
    bottleneck_device_id: str | None
    devices: list[DevicePlan]
    backups: list[BackupPlan]
    recommendations: list[Recommendation]
    assumptions: list[str]


class PlanResponse(BaseModel):
    """`{ plan }`."""

    plan: Plan


# --- Outages -----------------------------------------------------------------------------


class Milestone(ApiModel):
    """A point on the outage timeline."""

    key: MilestoneKey
    at: UtcDateTime
    title: str
    detail: str
    severity: Severity
    passed: bool


class Ack(ApiModel):
    """A caregiver's acknowledgement of an outage alert."""

    caregiver_id: str
    name: str
    message: str | None
    eta_minutes: float | None
    at: UtcDateTime


class _OutageBase(ApiModel):
    id: str
    status: OutageStatus
    source: OutageSource
    drill: bool
    speed: float
    started_at: UtcDateTime
    ended_at: UtcDateTime | None
    milestones: list[Milestone]
    acks: list[Ack]
    last_check_in_at: UtcDateTime
    last_check_in_ok: bool = Field(
        description="Additive: false when the owner's last check-in said they need help."
    )


class Outage(_OutageBase):
    """An outage (or drill) with its live plan and timeline."""

    plan: Plan


class OutageSummary(_OutageBase):
    """An outage in the history list (no `plan`)."""


class OutageResponse(BaseModel):
    """`{ outage }` (null when there is no current outage)."""

    outage: Outage | None


class OutageHistoryResponse(BaseModel):
    """`{ outages }`, newest first."""

    outages: list[OutageSummary]


class OutageCreate(RequestModel):
    """Body of `POST /api/households/{id}/outages`."""

    source: OutageSource
    speed: float = Field(default=1, ge=1, le=600)

    @model_validator(mode="after")
    def _speed_only_for_drills(self) -> "OutageCreate":
        if self.speed != 1 and self.source != "drill":
            raise ValueError("speed is only allowed for drills")
        return self


class DeviceChargeReport(RequestModel):
    """One device's battery level reported at check-in."""

    device_id: str
    charge_percent: Percent


class BackupChargeReport(RequestModel):
    """Additive: one backup's level reported at check-in."""

    backup_id: str
    charge_percent: Percent | None = None
    fuel_hours: Annotated[float, Field(ge=0, le=500)] | None = None


class CheckInRequest(RequestModel):
    """Body of `POST .../outages/current/check-in`."""

    ok: bool = True
    devices: list[DeviceChargeReport] | None = Field(default=None, max_length=50)
    backups: list[BackupChargeReport] | None = Field(
        default=None, max_length=20, description="Additive: backup levels, if known."
    )


class AckRequest(RequestModel):
    """Body of `POST .../outages/current/ack`."""

    message: str | None = Field(default=None, max_length=280)
    eta_minutes: Annotated[float, Field(ge=0, le=1440)] | None = None


# --- Caregivers --------------------------------------------------------------------------


class JoinRequest(RequestModel):
    """Body of `POST /api/caregivers/join`."""

    join_code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=60)
    relationship: str | None = Field(default=None, max_length=60)


class CaregiverJoinedResponse(BaseModel):
    """`201 { caregiver, caregiver_token, household }`."""

    caregiver: Caregiver
    caregiver_token: str
    household: Household


# --- Help and preparation ----------------------------------------------------------------


class HelpSite(ApiModel):
    """A nearby place that may have power."""

    id: str
    name: str
    kind: HelpSiteKind
    lat: float
    lon: float
    distance_km: float
    travel_minutes: int
    likely_has_power: bool
    address: str | None


class HelpSitesResponse(BaseModel):
    """`{ sites, source, note? }`."""

    sites: list[HelpSite]
    source: Literal["openstreetmap", "fallback"]
    note: str | None = None


class MedicalProgramOut(ApiModel):
    """A utility's medical / life-support customer program."""

    utility: str
    states: list[str]
    program_name: str
    what_it_does: str
    how_to_apply: str
    url: str
    verified_at: str


class MedicalProgramsResponse(BaseModel):
    """`{ programs, generic_advice }`."""

    programs: list[MedicalProgramOut]
    generic_advice: list[str]


class MedicalLetter(BaseModel):
    """`{ subject, body }`: a letter to the utility, ready to print and sign."""

    subject: str
    body: str


class EmergencyCardDevice(BaseModel):
    """One device line on the emergency card."""

    label: str
    life_sustaining: bool
    draw_watts: float
    internal_minutes: float


class EmergencyCardCaregiver(BaseModel):
    """One caregiver line on the emergency card."""

    name: str
    relationship: str | None


class EmergencyCard(BaseModel):
    """A one-page summary to stick on the fridge or hand to first responders."""

    patient_first_name: str
    address: str | None
    devices: list[EmergencyCardDevice]
    critical_runtime_minutes: float | None
    caregivers: list[EmergencyCardCaregiver]
    utility_name: str | None
    medical_program_registered: bool
    instructions: list[str]


# --- AI device helper and meta -----------------------------------------------------------


class IdentifyDeviceRequest(RequestModel):
    """JSON body of `POST /api/ai/identify-device`."""

    text: str = Field(min_length=1, max_length=500)


class DeviceMatch(BaseModel):
    """The device the helper thinks it is, with figures to pre-fill."""

    kind: DeviceKind
    label: str
    draw_watts: float
    internal_battery_wh: float


class IdentifyDeviceResponse(BaseModel):
    """`{ match, confidence, source, explanation }`."""

    match: DeviceMatch
    confidence: Confidence
    source: Literal["ai", "catalog"]
    explanation: str


class HealthResponse(BaseModel):
    """`GET /api/health`."""

    ok: bool
    version: str
    ai_enabled: bool = Field(description="Additive: whether photo/AI device identification works.")

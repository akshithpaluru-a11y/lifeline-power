"""Catalog of medical device kinds and backup power types, with typical (conservative) figures.

Every figure is only a starting point that the household can edit per device. Where makers publish
numbers we use them; where they don't, we lean towards *more* power use and *smaller* batteries, so
the app errs on the side of warning families earlier rather than later. The comment above each
entry says where its numbers come from (checked October 2026).
"""

from dataclasses import dataclass
from typing import Literal

DeviceKind = Literal[
    "oxygen_concentrator_home",
    "oxygen_concentrator_portable",
    "home_ventilator",
    "cpap_bipap",
    "suction_machine",
    "enteral_feeding_pump",
    "infusion_pump",
    "power_wheelchair",
    "nebulizer",
    "medication_fridge",
    "home_dialysis",
    "pulse_oximeter_monitor",
    "pressure_mattress_pump",
    "custom",
]
DeviceCategory = Literal[
    "breathing", "feeding", "mobility", "monitoring", "therapy", "cooling", "other"
]
DeviceIcon = Literal[
    "oxygen",
    "oxygen_portable",
    "ventilator",
    "cpap",
    "suction",
    "feeding_pump",
    "infusion_pump",
    "wheelchair",
    "nebulizer",
    "fridge",
    "dialysis",
    "monitor",
    "bed",
    "device",
]
BackupType = Literal["power_station", "ups", "generator", "car_inverter"]
BackupIcon = Literal["power_station", "ups", "generator", "car"]

FUEL_BACKUP_TYPES: frozenset[str] = frozenset({"generator", "car_inverter"})
"""Backups limited by fuel (hours) rather than by a battery (watt-hours)."""


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """Typical figures for one kind of medical device."""

    kind: DeviceKind
    name: str
    category: DeviceCategory
    icon: DeviceIcon
    life_sustaining_default: bool
    typical_draw_watts: float
    saver_draw_watts: float | None
    saver_tip: str | None
    saver_action: str | None
    """Title template for the saver-mode tip, e.g. `Turn off the humidifier on {label}`."""
    internal_battery_wh: float
    duty_cycle: float
    ac_powered: bool
    description: str
    keywords: tuple[str, ...]
    """Words people use for this kind of device (used by the device identifier)."""
    runs_in_short_bursts: bool = False
    """True when the duty cycle is made of short bursts spread through the day (suction, a
    fridge compressor), so the daily average is a fair guide to how fast it drains a battery.
    False when the device can run for hours at a stretch (an overnight BiPAP or dialysis
    session, a feed): the planner then counts a life-sustaining one at its full draw, because
    the power can go out just as it is switched on."""
    emergency_backup: str = "your care team's emergency backup plan"
    """What to switch to while the device has no power, as in "Switch now to ..."."""
    saver_outage_action: str = "switch {label} to its lower-power setting"
    """The saver tip as a step to take during an outage (after a care-team caveat)."""


@dataclass(frozen=True, slots=True)
class BackupSpec:
    """Typical figures for one kind of backup power source."""

    type: BackupType
    name: str
    icon: BackupIcon
    typical_capacity_wh: float | None
    typical_max_output_watts: float
    has_ac_outlet: bool
    typical_fuel_hours: float | None
    description: str


_DEVICES: tuple[DeviceSpec, ...] = (
    # Philips EverFlo spec sheet: "average power consumption 350 W"; Colorado Country Life
    # (co-op magazine) quotes 280-350 W for concentrators. No default saver figure: the EverFlo
    # publishes a single average figure, and a lower flow saves little on such fixed-compressor
    # units, so assuming one would make countdowns too long. Some newer units do save a lot
    # (Inogen At Home lists 100 W on setting 2); families can enter their own model's figure.
    # The tip and action stay so that a figure a family enters still carries the care-team check.
    DeviceSpec(
        kind="oxygen_concentrator_home",
        emergency_backup="oxygen cylinders if you have them",
        saver_outage_action="switch {label} to a lower flow setting",
        name="Home oxygen concentrator",
        category="breathing",
        icon="oxygen",
        life_sustaining_default=True,
        typical_draw_watts=350,
        saver_draw_watts=None,
        saver_tip="Use a lower flow setting only if your care team has said it is safe for you, "
        "and keep backup oxygen cylinders from your supplier.",
        saver_action="Ask your care team about a lower flow setting for {label}",
        internal_battery_wh=0,
        duty_cycle=1.0,
        ac_powered=True,
        description="Stationary concentrators plug into the wall and have no battery. Most use "
        "280-350 W, so they drain a power station fast, and many save little power at a lower "
        "flow. Check your device manual for its exact power use.",
        keywords=("oxygen", "concentrator", "o2", "everflo", "perfecto", "lpm", "liter"),
    ),
    # Inogen One G5: single battery "up to 6.5 hours" on setting 1 (Inogen spec sheet);
    # replacement single packs are rated 14.4 V / 92 Wh. 30 W is a cautious draw for pulse
    # settings 2-3. Portable units run from their battery or a DC car adapter, so no inverter.
    DeviceSpec(
        kind="oxygen_concentrator_portable",
        emergency_backup="oxygen cylinders if you have them",
        saver_outage_action="switch {label} to a lower pulse setting",
        name="Portable oxygen concentrator",
        category="breathing",
        icon="oxygen_portable",
        life_sustaining_default=True,
        typical_draw_watts=30,
        saver_draw_watts=20,
        saver_tip="A lower pulse setting makes the battery last longer. Use one only if your care "
        "team has said it is safe for you.",
        saver_action="Ask your care team about a lower pulse setting on {label}",
        internal_battery_wh=92,
        duty_cycle=1.0,
        ac_powered=False,
        description="Battery-powered units such as the Inogen One or Philips SimplyGo. Battery "
        "life depends a lot on the setting, so carry spare batteries and a car charger. Check "
        "your device manual for real run times.",
        keywords=("portable oxygen", "poc", "inogen", "simplygo", "oxygo", "pulse dose"),
    ),
    # ResMed Astral 100/150: 95 Wh internal battery, about 8 h (resmed.asia product page);
    # Philips Trilogy detachable battery 14.4 V / 98 Wh; Trilogy Evo 7.5 h per battery.
    # 20 W is deliberately above what those run times imply.
    DeviceSpec(
        kind="home_ventilator",
        emergency_backup="a manual resuscitation bag (Ambu bag) if you have one",
        name="Home ventilator",
        category="breathing",
        icon="ventilator",
        life_sustaining_default=True,
        typical_draw_watts=20,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=95,
        duty_cycle=1.0,
        ac_powered=True,
        description="Home ventilators (Trilogy, Astral, LTV and others) have an internal battery "
        "and often a detachable spare. Real draw depends on your settings; your respiratory "
        "therapist or the device manual can give a better number.",
        keywords=("ventilator", "vent", "trilogy", "astral", "ltv", "vivo", "breathing machine"),
    ),
    # ResMed's measured AirSense 10 consumption: 7 W device only, 22 W with humidifier, 34 W with
    # humidifier + heated tube (ResMed "Power consumption Air10 devices" sheet). Used about
    # 8 hours a day, hence the duty cycle.
    DeviceSpec(
        kind="cpap_bipap",
        saver_outage_action="turn off the humidifier on {label}",
        name="CPAP / BiPAP",
        category="breathing",
        icon="cpap",
        life_sustaining_default=False,
        typical_draw_watts=40,
        saver_draw_watts=10,
        saver_tip="The humidifier and heated tube use most of the power. Turning them off cuts "
        "the machine's use to about a quarter.",
        saver_action="Turn off the humidifier on {label}",
        internal_battery_wh=0,
        duty_cycle=0.35,
        ac_powered=True,
        description="Counted as running about 8 hours a day. Many people can skip a night of "
        "CPAP in an outage, but some BiPAP users can't: ask your doctor and mark it "
        "life-sustaining if you depend on it. A life-sustaining one is counted at its full "
        "draw, because the power can go out just as it is switched on for the night.",
        keywords=("cpap", "bipap", "apap", "sleep apnea", "airsense", "dreamstation", "aircurve"),
    ),
    # UNFPA product specification for portable electric aspirators: about 100 W, battery
    # runtime up to 60 minutes. Suction is used in short bursts, so we count 10% of the time.
    DeviceSpec(
        kind="suction_machine",
        emergency_backup="a manual suction device if you have one",
        name="Suction machine",
        category="breathing",
        icon="suction",
        life_sustaining_default=True,
        typical_draw_watts=100,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=0.1,
        ac_powered=True,
        description="Used in short bursts, so we count it as running 10% of the time. Portable "
        "units often have a battery good for about an hour of suction: enter it if yours does, "
        "and keep a manual suction device as a backup.",
        keywords=("suction", "aspirator", "vacu-aide", "secretions"),
        runs_in_short_bursts=True,
    ),
    # Kangaroo Joey (Cardinal Health spec): battery lasts at least 18 h at 125 mL/h, 3.7 V
    # Li-ion; capacity isn't published, so we assume 10 Wh and a cautious 0.75 W (about 12 h).
    DeviceSpec(
        kind="enteral_feeding_pump",
        emergency_backup="gravity or syringe feeding if your care team has shown you how",
        name="Feeding pump",
        category="feeding",
        icon="feeding_pump",
        life_sustaining_default=True,
        typical_draw_watts=0.75,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=10,
        duty_cycle=1.0,
        ac_powered=True,
        description="Feeding pumps sip power and usually run many hours on their battery. Keep "
        "it charged, and ask your care team about gravity or syringe feeding as a backup.",
        keywords=("feeding", "enteral", "kangaroo", "joey", "g-tube", "tube feed", "epump"),
    ),
    # No single published figure; ambulatory infusion pumps typically run 12+ hours on their
    # batteries. 2 W with a 10 Wh battery (about 4.5 h) is a cautious stand-in.
    DeviceSpec(
        kind="infusion_pump",
        name="Infusion pump",
        category="therapy",
        icon="infusion_pump",
        life_sustaining_default=True,
        typical_draw_watts=2,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=10,
        duty_cycle=1.0,
        ac_powered=True,
        description="IV and medication pumps run on batteries for hours. Check the battery "
        "life in your pump's manual and keep spare batteries if it takes them.",
        keywords=("infusion", "iv pump", "cadd", "tpn", "medication pump"),
    ),
    # 1800wheelchair.com battery guide: travel chairs carry 144-288 Wh for 8-15 miles; full-size
    # chairs carry two 12 V batteries (roughly 1 kWh). 600 Wh is a cautious middle value.
    DeviceSpec(
        kind="power_wheelchair",
        name="Power wheelchair",
        category="mobility",
        icon="wheelchair",
        life_sustaining_default=False,
        typical_draw_watts=150,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=600,
        duty_cycle=0.15,
        ac_powered=True,
        description="Counted as driving about 15% of the time. Charge it fully whenever a storm "
        "is forecast; full-size chairs carry about 1 kWh of battery, folding chairs much less.",
        keywords=("wheelchair", "power chair", "scooter", "permobil", "jazzy", "mobility"),
        runs_in_short_bursts=True,
    ),
    # Philips InnoSpire Essence: 120 V, 1.7 A, 5-7 minute treatments (Philips product page).
    # 1.7 A x 120 V is about 200 VA; 150 W is a cautious real-power figure.
    DeviceSpec(
        kind="nebulizer",
        name="Nebulizer",
        category="breathing",
        icon="nebulizer",
        life_sustaining_default=False,
        typical_draw_watts=150,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=0.05,
        ac_powered=True,
        description="Treatments take a few minutes, so we count about 5% of the time. Ask your "
        "doctor about a battery-powered mesh nebulizer or an inhaler with spacer as a backup.",
        keywords=("nebulizer", "neb", "innospire", "pari", "breathing treatment"),
        runs_in_short_bursts=True,
    ),
    # Small refrigerators run roughly 60-100 W while the compressor is on and cycle about a
    # third of the time. USDA: a closed refrigerator keeps food cold for about 4 hours.
    DeviceSpec(
        kind="medication_fridge",
        name="Medication fridge",
        category="cooling",
        icon="fridge",
        life_sustaining_default=False,
        typical_draw_watts=80,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=0.35,
        ac_powered=True,
        description="Keeps insulin and other medicines cold. A closed fridge stays cold for about "
        "4 hours; check each medicine's label for how long it can be kept at room temperature.",
        keywords=("fridge", "refrigerator", "insulin", "medication cooler", "vaccine"),
        runs_in_short_bursts=True,
    ),
    # Peritoneal dialysis cyclers warm the fluid with a heater; makers don't publish average
    # draw. 200 W while running overnight (about 40% of the day) is a cautious estimate. Home
    # hemodialysis machines can use far more.
    DeviceSpec(
        kind="home_dialysis",
        emergency_backup="your dialysis center's emergency plan",
        name="Home dialysis machine",
        category="therapy",
        icon="dialysis",
        life_sustaining_default=True,
        typical_draw_watts=200,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=0.4,
        ac_powered=True,
        description="Runs overnight (about 40% of the day). The outage countdown counts it at its "
        "full draw, because the power can go out just as a treatment starts. Ask your dialysis "
        "center now what to do in an outage: many peritoneal patients can do manual exchanges, "
        "and centers keep emergency plans. Check the manual for your machine's power use.",
        keywords=("dialysis", "cycler", "peritoneal", "hemodialysis", "nxstage", "homechoice"),
    ),
    # Bedside pulse oximeters and apnea monitors draw a few watts; most have a few hours of
    # battery. 5 W with a 10 Wh battery is a cautious stand-in.
    DeviceSpec(
        kind="pulse_oximeter_monitor",
        name="Pulse oximeter / monitor",
        category="monitoring",
        icon="monitor",
        life_sustaining_default=False,
        typical_draw_watts=5,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=10,
        duty_cycle=1.0,
        ac_powered=True,
        description="Bedside oximeters and apnea monitors use little power and usually have a "
        "battery. A fingertip oximeter on AAA batteries is a cheap backup.",
        keywords=("oximeter", "pulse ox", "spo2", "monitor", "apnea monitor", "masimo", "nonin"),
    ),
    # Alternating-pressure mattress pumps: one listing shows 8.5 W for an 8 LPM pump (Vitality
    # Medical, Ultra Care Excel); larger pumps use more, so we assume 15 W.
    DeviceSpec(
        kind="pressure_mattress_pump",
        name="Pressure-relief mattress pump",
        category="therapy",
        icon="bed",
        life_sustaining_default=False,
        typical_draw_watts=15,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=1.0,
        ac_powered=True,
        description="Air mattresses lose pressure slowly when the pump stops; many have a "
        "transport or static setting that holds air. Check your manual.",
        keywords=("mattress", "air mattress", "pressure relief", "alternating pressure", "bed"),
    ),
    DeviceSpec(
        kind="custom",
        name="Other medical device",
        category="other",
        icon="device",
        life_sustaining_default=False,
        typical_draw_watts=50,
        saver_draw_watts=None,
        saver_tip=None,
        saver_action=None,
        internal_battery_wh=0,
        duty_cycle=1.0,
        ac_powered=True,
        description="Enter the watts from the device's label or manual (watts = volts x amps).",
        keywords=(),
    ),
)

_BACKUPS: tuple[BackupSpec, ...] = (
    # Jackery Explorer 1000 v2: 1070 Wh, 1500 W AC output (retailer listings).
    BackupSpec(
        type="power_station",
        name="Portable power station",
        icon="power_station",
        typical_capacity_wh=1000,
        typical_max_output_watts=1500,
        has_ac_outlet=True,
        typical_fuel_hours=None,
        description="A big rechargeable battery with wall-style outlets (Jackery, EcoFlow, "
        "Anker and others). Safe indoors. Keep it charged and check it every few months.",
    ),
    # APC Back-UPS Pro 1500VA (BR1500MS2) is rated 900 W. Battery size isn't published in Wh;
    # this class uses lead-acid batteries of roughly 200 Wh, and less is usable under load.
    BackupSpec(
        type="ups",
        name="UPS battery backup",
        icon="ups",
        typical_capacity_wh=150,
        typical_max_output_watts=900,
        has_ac_outlet=True,
        typical_fuel_hours=None,
        description="Computer-style battery backups are made to bridge a few minutes, not "
        "hours. Fine for a ventilator top-up, not for an oxygen concentrator.",
    ),
    # Honda EU2200i: 1800 W rated, 8.1 h per tank at 1/4 load, 3.2 h at rated load.
    BackupSpec(
        type="generator",
        name="Portable generator",
        icon="generator",
        typical_capacity_wh=None,
        typical_max_output_watts=1800,
        has_ac_outlet=True,
        typical_fuel_hours=8,
        description="Runs as long as you have fuel. Only ever run it outdoors, far from doors "
        "and windows: generator exhaust contains deadly carbon monoxide.",
    ),
    # Plug-in inverters for a car's 12 V socket are typically rated about 150 W (e.g. Tripp
    # Lite PV150). A car idles on roughly 0.2-0.5 gallons per hour (US DOE AFDC), so 10 hours
    # is a few gallons.
    BackupSpec(
        type="car_inverter",
        name="Car with power inverter",
        icon="car",
        typical_capacity_wh=None,
        typical_max_output_watts=150,
        has_ac_outlet=True,
        typical_fuel_hours=10,
        description="An inverter in the car's 12 V socket can run small devices while the "
        "engine idles. Never run the car in a garage, even with the door open.",
    ),
)

DEVICE_CATALOG: dict[str, DeviceSpec] = {spec.kind: spec for spec in _DEVICES}
BACKUP_CATALOG: dict[str, BackupSpec] = {spec.type: spec for spec in _BACKUPS}


def device_spec(kind: str) -> DeviceSpec:
    """The catalog entry for a device kind (unknown kinds fall back to `custom`)."""
    return DEVICE_CATALOG.get(kind, DEVICE_CATALOG["custom"])


def backup_spec(backup_type: str) -> BackupSpec:
    """The catalog entry for a backup type."""
    return BACKUP_CATALOG[backup_type]

"""Printable documents: the medical-customer letter and the one-page emergency card.

The letter asks the utility to register the home as a medical / life-support customer; the card
is a summary for the fridge door. Both are pure functions over the household and its plan.
"""

from datetime import date

from app.schemas import (
    EmergencyCard,
    EmergencyCardCaregiver,
    EmergencyCardDevice,
    Household,
    MedicalLetter,
    Plan,
)
from app.timeutil import format_duration
from app.utilities_data import MedicalProgram

_BLANK = "______________________"


def medical_letter(
    household: Household, plan: Plan, program: MedicalProgram | None, today: date
) -> MedicalLetter:
    """A letter to the utility asking to register the home as a medical / life-support customer.

    It lists the household's devices and leaves blanks for the account details and the
    clinician's certification and signature, ready to print and take to an appointment.
    """
    utility = household.utility_name or (program.utility if program else None)
    address = household.address or _BLANK
    patient = household.patient_first_name
    runtimes = {entry.device_id: entry for entry in plan.devices}
    device_lines = []
    for device in household.devices:
        entry = runtimes.get(device.id)
        battery = (
            f", runs about {format_duration(entry.internal_minutes)} on its own battery"
            if entry and entry.internal_minutes > 0
            else ", no battery of its own"
        )
        needed = " (life-sustaining)" if device.life_sustaining else ""
        device_lines.append(f"  - {device.label}{needed}: about {device.draw_watts:g} W{battery}")
    devices_text = "\n".join(device_lines) or f"  - {_BLANK} (list the equipment)"
    program_ref = f" under your {program.program_name}" if program else ""
    program_note = (
        f"\nNote: {program.utility} may also ask for its own {program.program_name} form. "
        f"You can find it here: {program.url}\n"
        if program
        else ""
    )

    body = f"""{today:%B} {today.day}, {today.year}

To: {utility or "[Your electric utility]"}, Customer Service

Re: Request for medical / life-support customer status
Account holder: {_BLANK}
Account number: {_BLANK}
Service address: {address}

Dear {utility or "utility"} customer service team,

Please register our home as a medical / life-support customer{program_ref}. {patient} lives at
the address above and depends on this electrically powered medical equipment:

{devices_text}

Losing power puts {patient}'s health at serious risk. We ask you to:
  - add this account to your list of customers who rely on medical equipment;
  - contact us before any planned outage or public-safety power shutoff;
  - note the equipment on our account for outage and disconnection decisions.

You can reach us at {_BLANK} (phone) or {_BLANK} (email).

Thank you,


{_BLANK}
Account holder signature and date

------------------------------------------------------------
CLINICIAN CERTIFICATION

I certify that {_BLANK} (patient's full name), date of birth {_BLANK}, has a
medical condition that requires the electrically powered equipment listed above, and that
losing electric service could seriously harm their health.

Clinician name and credentials: {_BLANK}
License number and state: {_BLANK}
Practice name and phone: {_BLANK}

Signature: {_BLANK}    Date: {_BLANK}
{program_note}"""
    subject = (
        f"Medical / life-support customer request for {household.address}"
        if household.address
        else "Medical / life-support customer request"
    )
    return MedicalLetter(subject=subject, body=body)


def emergency_card(household: Household, plan: Plan) -> EmergencyCard:
    """A one-page summary for the fridge door, a go-bag or first responders."""
    runtimes = {entry.device_id: entry for entry in plan.devices}
    devices = [
        EmergencyCardDevice(
            label=device.label,
            life_sustaining=device.life_sustaining,
            draw_watts=device.draw_watts,
            internal_minutes=runtimes[device.id].internal_minutes if device.id in runtimes else 0,
        )
        for device in household.devices
    ]
    return EmergencyCard(
        patient_first_name=household.patient_first_name,
        address=household.address,
        devices=devices,
        critical_runtime_minutes=plan.critical_runtime_minutes,
        caregivers=[
            EmergencyCardCaregiver(name=c.name, relationship=c.relationship)
            for c in household.caregivers
        ],
        utility_name=household.utility_name,
        medical_program_registered=household.medical_program_registered,
        instructions=_instructions(household, plan),
    )


def _instructions(household: Household, plan: Plan) -> list[str]:
    """Step-by-step outage instructions tailored to this household."""
    critical = [d for d in household.devices if d.life_sustaining]
    labels = {d.id: d.label for d in household.devices}
    steps = []
    if critical:
        names = ", ".join(d.label for d in critical)
        steps.append(f"When the power goes out, check that {names} kept running.")
    batteries = [b for b in household.backups if b.type in ("power_station", "ups")]
    if batteries and critical:
        steps.append(
            f"Plug life-sustaining devices into {batteries[0].label} first; unplug anything else."
        )
    steps.append("Open LifeLine Power and check in every hour so caregivers know you are OK.")
    if plan.critical_runtime_minutes is not None and plan.bottleneck_device_id:
        steps.append(
            f"With full batteries, {labels.get(plan.bottleneck_device_id, 'the first device')} "
            f"lasts about {format_duration(plan.critical_runtime_minutes)}. Leave for a place "
            "with power well before then; the app shows your leave-by time."
        )
    if any(b.type in ("generator", "car_inverter") for b in household.backups):
        steps.append(
            "Run generators and cars outdoors only, at least 20 feet from doors and windows."
        )
    if household.utility_name:
        steps.append(f"Report the outage to {household.utility_name}.")
    steps.append(
        "Call 911 if a life-sustaining device is about to run out and you can't reach power."
    )
    return steps

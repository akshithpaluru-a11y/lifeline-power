"""Utility programs for customers who depend on powered medical equipment.

Each entry below was checked against the utility's own web page (or official form) on
2026-10-14; `url` is the exact page that was read. Programs differ a lot: some give extra
energy at the lowest rate (California "Medical Baseline"), most give advance warning of planned
shutoffs and extra care before a disconnection for unpaid bills. None of them promise that
power will stay on or be restored first, so the advice always pairs registration with a backup
plan.
"""

import re
from dataclasses import dataclass

VERIFIED_AT = "2026-10-14"


@dataclass(frozen=True, slots=True)
class MedicalProgram:
    """One utility's medical / life-support customer program."""

    utility: str
    aliases: tuple[str, ...]
    """Other names people type for the utility (matched case- and punctuation-insensitively).
    Each must belong to this company alone, in every state."""
    states: tuple[str, ...]
    program_name: str
    what_it_does: str
    how_to_apply: str
    url: str
    shared_names: tuple[str, ...] = ()
    """Names this utility shares with a sister company in another state ("National Grid" is also
    Massachusetts' utility). They only count once the household's state is known."""
    verified_at: str = VERIFIED_AT


PROGRAMS: tuple[MedicalProgram, ...] = (
    MedicalProgram(
        utility="Pacific Gas and Electric (PG&E)",
        aliases=("PG&E", "Pacific Gas and Electric", "Pacific Gas & Electric"),
        states=("CA",),
        program_name="Medical Baseline",
        what_it_does="Extra energy each month at the lowest price, and extra alerts before a "
        "Public Safety Power Shutoff. Based on medical need, not income.",
        how_to_apply="Apply online at pge.com/mblapp; PG&E then emails your doctor or other "
        "licensed medical practitioner to confirm. Paper forms can be mailed or emailed.",
        url="https://www.pge.com/medicalbaseline",
    ),
    MedicalProgram(
        utility="Southern California Edison (SCE)",
        aliases=("SCE", "Southern California Edison", "SoCal Edison"),
        states=("CA",),
        program_name="Medical Baseline Allowance",
        what_it_does="An extra 16.5 kWh a day at the lowest baseline rate (or a discount on "
        "time-of-use plans) for households that rely on powered medical equipment.",
        how_to_apply="Apply online and have your medical professional sign electronically, or "
        "mail the two-page form. Recertify every 2-4 years.",
        url="https://www.sce.com/residential/assistance/medical-baseline",
    ),
    MedicalProgram(
        utility="San Diego Gas & Electric (SDG&E)",
        aliases=("SDG&E", "SDGE", "San Diego Gas and Electric", "San Diego Gas & Electric"),
        states=("CA",),
        program_name="Medical Baseline Allowance",
        what_it_does="An extra allowance of electricity and gas at the lowest rates for "
        "households using qualifying devices such as CPAP, oxygen or dialysis machines.",
        how_to_apply="Fill in the application, have your physician certify it, and send it by "
        "email or mail. Renew every 2 years (or self-certify every 4 years for permanent "
        "conditions).",
        url="https://www.sdge.com/medicalbaseline",
    ),
    MedicalProgram(
        utility="Pacific Power",
        aliases=("Pacific Power",),
        states=("OR", "WA", "CA"),
        program_name="Medical Certificate program",
        what_it_does="Flags your account so your medical needs are considered during outages. "
        "In Oregon, enrolled income-qualified customers may get a backup power rebate.",
        how_to_apply="A doctor, PA or nurse practitioner completes the Medical Certificate "
        "form; submit it online or by mail or fax. Renewal follows each state's rules.",
        url="https://www.pacificpower.net/medical",
        shared_names=("PacifiCorp",),  # also Rocky Mountain Power in UT, WY and ID
    ),
    MedicalProgram(
        utility="Seattle City Light",
        aliases=("Seattle City Light", "SCL"),
        states=("WA",),
        program_name="Life Support Equipment Program",
        what_it_does="Personal notice of planned outages and of unplanned outages likely to "
        "last more than 8 hours, plus help keeping service on if you fall behind on bills.",
        how_to_apply="Print the enrollment form, have a licensed healthcare provider complete "
        "the certification, and mail or fax it. Renew every year.",
        url="https://seattle.gov/city-light/outages/outage-safety/life-support-equipment",
        shared_names=("City Light",),  # Tacoma Power was long called Tacoma City Light
    ),
    MedicalProgram(
        utility="Arizona Public Service (APS)",
        aliases=("APS", "Arizona Public Service"),
        states=("AZ",),
        program_name="Medical Care Program",
        what_it_does="Advance notice of planned outages by phone, letter, email or text for "
        "households that rely on life-saving medical equipment.",
        how_to_apply="Complete the application with a licensed medical professional and mail "
        "it to APS (it must go by mail).",
        url="https://aps.com/en/Residential/Account/Assistance-Programs/Medical-Equipment-Preparedness",
    ),
    MedicalProgram(
        utility="Omaha Public Power District (OPPD)",
        aliases=("OPPD", "Omaha Public Power District", "Omaha Public Power"),
        states=("NE",),
        program_name="Life-Sustaining Equipment (LSE) Program",
        what_it_does="Advance notice before planned outages, outreach if your account is "
        "pending disconnection, and outage notices for a caregiver you add as a guest user.",
        how_to_apply="Print the LSE enrollment form, complete it with your healthcare "
        "professional (both of you sign), and email it to LSE@OPPD.com.",
        url="https://oppd.com/residential/customer-service/life-sustaining-equipment-program/",
    ),
    MedicalProgram(
        utility="CenterPoint Energy (Houston electric)",
        aliases=("CenterPoint Houston", "CenterPoint Energy Houston"),
        states=("TX",),
        program_name="Critical Care Customers",
        what_it_does="Notice of planned outages that may affect you or your medical devices. "
        "Registration doesn't guarantee uninterrupted power.",
        how_to_apply="Complete Texas' Critical Care / Chronic Condition application with your "
        "physician. In Texas the form goes to your wires company (CenterPoint, Oncor, AEP Texas "
        "or TNMP), not your retail electric provider.",
        url="https://www.centerpointenergy.com/en-us/residential/customer-service/critical-care-customers?sa=HO",
        shared_names=("CenterPoint", "CenterPoint Energy", "CNP"),  # also Indiana and Ohio
    ),
    MedicalProgram(
        utility="Entergy",
        aliases=(
            "Entergy",
            "Entergy Arkansas",
            "Entergy Louisiana",
            "Entergy Mississippi",
            "Entergy New Orleans",
            "Entergy Texas",
        ),
        states=("AR", "LA", "MS", "TX"),
        program_name="Medical needs program",
        what_it_does="Puts your home on Entergy's medical needs list. It's for ventilators or "
        "LVADs used 24 hours a day and hospice patients; it doesn't guarantee priority "
        "restoration.",
        how_to_apply="Call 1-800-368-3749 and explain the medical need; your physician then "
        "faxes a statement on letterhead to the number the agent gives you.",
        url="https://www.entergy.com/stormcenter/medical",
    ),
    MedicalProgram(
        utility="Ameren Missouri",
        aliases=("Ameren Missouri", "Ameren UE"),
        states=("MO",),
        program_name="Medical Equipment Registry",
        what_it_does="Advance notice of planned outages so you can arrange other power for "
        "medical equipment. No priority restoration and no protection from disconnection.",
        how_to_apply="Fill in the customer section, have a licensed medical professional "
        "complete the provider statement, and mail, fax or email it to Ameren Missouri.",
        url="https://www.ameren.com/-/media/missouri-site/files/energy-assistance/medical-equipment-registry-application.ashx",
        shared_names=("Ameren",),  # also Ameren Illinois
    ),
    MedicalProgram(
        utility="Duke Energy",
        aliases=(
            "Duke",
            "Duke Energy",
            "Duke Energy Carolinas",
            "Duke Energy Progress",
            "Duke Energy Florida",
        ),
        states=("NC", "SC", "FL", "IN", "OH", "KY"),
        program_name="Medically Essential program",
        what_it_does="Marks your account and meter so a disconnection for nonpayment is handled "
        "with extra care. It doesn't mean you'll be restored first after a storm.",
        how_to_apply="Contact Duke Energy or complete the form on the program page.",
        url="https://www.duke-energy.com/home/billing/special-assistance/medically-essential",
    ),
    MedicalProgram(
        utility="Florida Power & Light (FPL)",
        aliases=("FPL", "Florida Power & Light", "Florida Power and Light"),
        states=("FL",),
        program_name="Medically Essential Service",
        what_it_does="Special notices before and after hurricanes and before a disconnection "
        "for nonpayment. It does not provide priority restoration after a storm.",
        how_to_apply="Submit the Essential Service form online or by mail; a Florida-licensed "
        "physician completes Part B. Reapply every year.",
        url="https://www.fpl.com/support/medically-essential-service.html",
    ),
    MedicalProgram(
        utility="Dominion Energy Virginia",
        aliases=("Dominion Energy Virginia", "Dominion Virginia"),
        states=("VA",),
        program_name="Medical conditions notation (Serious Medical Condition Certification)",
        what_it_does="Adds a medical notation to your account so Dominion knows powered medical "
        "equipment is used in the home.",
        how_to_apply="Have your physician complete the Serious Medical Condition Certification "
        "Form and email it to demedical@domenergy.com. Send a new form every year.",
        url="https://www.dominionenergy.com/Virginia/Start-Stop-Service/Medical-Conditions?sc_lang=en",
        shared_names=("Dominion", "Dominion Energy"),  # also Dominion Energy South Carolina
    ),
    MedicalProgram(
        utility="Public Service Electric and Gas (PSE&G)",
        aliases=(
            "Public Service Electric and Gas",
            "Public Service Electric & Gas",
            "PSEG New Jersey",
            "PSEG NJ",
        ),
        states=("NJ",),
        program_name="Life-Sustaining Equipment (Critical Care) program",
        what_it_does="Advance phone calls when severe weather makes outages likely, and outage "
        "and restoration calls during outages in your area.",
        how_to_apply="Have the prescribing licensed medical professional complete and sign the "
        "Critical Care application, then return it to PSE&G. Renew every year.",
        url="https://nj.pseg.com/saveenergyandmoney/gethelppayingyourbill/additionalresources",
        # Also PSEG Long Island ("PSE&G" and "PSEG" can't be told apart once punctuation goes).
        shared_names=("PSE&G", "PSEG"),
    ),
    MedicalProgram(
        utility="Con Edison",
        aliases=("Con Edison", "ConEd", "Con Ed", "Consolidated Edison"),
        states=("NY",),
        program_name="Life-Support Equipment registration",
        what_it_does="Lets Con Edison reach you in an emergency or power outage. Register even "
        "if electricity is included in your rent.",
        how_to_apply="Register in My Account, by phone at 1-877-582-6633, or by emailing the "
        "form. A doctor, NP, PA or local board of health must certify the equipment within 30 "
        "days.",
        url="https://www.coned.com/SpecialServices",
    ),
    MedicalProgram(
        utility="National Grid (Upstate New York)",
        aliases=("Niagara Mohawk", "NiMo"),
        states=("NY",),
        program_name="Life Support Program",
        what_it_does="Advance contact about planned outages, and during long unplanned outages "
        "two phone attempts followed by a wellness check if they can't reach you.",
        how_to_apply="Apply online with the Protections Application (or email, fax or mail the "
        "form); National Grid sends a Physician Protection Form to your clinician. Recertify "
        "every year.",
        url="https://www.nationalgridus.com/Upstate-NY-Home/Storms-Outages/Life-Sustaining-Equipment",
        shared_names=("National Grid",),  # also Massachusetts
    ),
    MedicalProgram(
        utility="Central Hudson Gas & Electric",
        aliases=("Central Hudson", "Central Hudson Gas & Electric", "CHG&E", "CHGE"),
        states=("NY",),
        program_name="Life Support Equipment Program",
        what_it_does="Codes your account and tags your meter as life-support, gives you a "
        "confidential hotline number, and adds protections and notifications.",
        how_to_apply="Complete the application including your attending physician's "
        "certification. Central Hudson mails a form each year for recertification.",
        url="https://centralhudson.com/en/account-resources/assistance-programs/life-support-apparatus-program/",
    ),
    MedicalProgram(
        utility="Hawaiian Electric",
        aliases=("Hawaiian Electric", "HECO", "Hawaii Electric Light", "Maui Electric"),
        states=("HI",),
        program_name="Life Support Program",
        what_it_does="Case-by-case billing help and advance notice before any disconnection; "
        "the utility keeps a record of homes relying on life-support devices.",
        how_to_apply="Complete the Life Support Enrollment Form, have a licensed physician sign "
        "it, and send it by regular mail or in person (not electronically).",
        url="https://www.hawaiianelectric.com/billing-and-payment/payment-assistance/life-support",
    ),
)

GENERIC_ADVICE: tuple[str, ...] = (
    "Call your electric utility and ask if it has a medical certificate, life-support or "
    "critical-care customer list. Most utilities do, even if it goes by a different name.",
    "Expect a form that a doctor, nurse practitioner or other licensed clinician must sign. "
    "LifeLine Power can draft a request letter for you to take to your appointment.",
    "Registration usually gets you warnings before planned shutoffs and extra protection from "
    "disconnection for unpaid bills. It almost never means your power is restored first, so you "
    "still need a backup plan.",
    "Mark your calendar to renew: many utilities make you recertify every year.",
    "Keep your phone number and email up to date with the utility so outage notices reach you.",
    "If you rent and electricity is included in your rent, ask the utility anyway: many let you "
    "register.",
    "Ask your county or city emergency management office whether it keeps a voluntary registry "
    "of residents who rely on powered medical equipment.",
    "Ask your medical equipment supplier about spare batteries or backup oxygen cylinders before "
    "storm season.",
    "If a life-sustaining device loses power and you can't get to a place with power, call 911.",
)


def _normalize(name: str) -> str:
    """Lower-case and strip everything but letters and digits (`PG&E` -> `pge`)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_MIN_PREFIX_LENGTH = 6
"""Names at least this long (normalized) also match typed names that start with them."""


def _matches_utility(program: MedicalProgram, utility: str, *, state_known: bool) -> bool:
    """True when the typed utility name refers to this program's utility."""
    typed = _normalize(utility)
    if not typed:
        return False
    names = (program.utility, *program.aliases, *(program.shared_names if state_known else ()))
    # Exact match, or a typed name that starts with a longer name ("Duke Energy Progress NC" ->
    # Duke Energy). Never a name in the middle ("Tacoma City Light" is not Seattle's "City
    # Light"), and short names such as "SCE" must match exactly.
    return any(
        typed == name or (len(name) >= _MIN_PREFIX_LENGTH and typed.startswith(name))
        for name in map(_normalize, names)
    )


def find_programs(state: str | None = None, utility: str | None = None) -> list[MedicalProgram]:
    """Programs matching an optional state (2-letter) and an optional utility name.

    With a utility, only that utility's programs are returned, so a family is never pointed at
    another company's program: if a state is given, only when the utility serves that state;
    without one, only for names no sister company in another state shares ("Ameren Missouri"
    matches, "Ameren" alone does not, because Ameren Illinois exists). With only a state, every
    program in that state is returned. With neither, all programs are returned.
    """
    state_code = state.upper() if state else None
    results = [p for p in PROGRAMS if state_code is None or state_code in p.states]
    if utility:
        known = state_code is not None
        results = [p for p in results if _matches_utility(p, utility, state_known=known)]
    return results

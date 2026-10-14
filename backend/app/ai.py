"""Identify a medical device from its name or a photo of its label.

A typed name that matches a popular model we have checked figures for is answered from that list.
Otherwise, with an Anthropic API key, Claude reads the text or photo and answers in a fixed JSON
shape. Its answer is never taken on trust: if it names one of the checked models we use our
figures, and any other figure that would make the device look longer-lasting than its kind can
be is corrected. Without a key (or if the call fails) we fall back to matching the text against
popular models and the catalog's keywords, which handles most typed names.
"""

import base64
import difflib
import logging
import re
from dataclasses import dataclass
from typing import Literal

import anthropic
from anthropic.types import ContentBlockParam, ImageBlockParam
from pydantic import BaseModel, ValidationError

from app.catalog import DEVICE_CATALOG, DeviceKind, device_spec
from app.errors import bad_request, unavailable
from app.schemas import Confidence, DeviceMatch, IdentifyDeviceResponse

logger = logging.getLogger(__name__)

ImageMediaType = Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def sniff_image_type(data: bytes) -> ImageMediaType | None:
    """The photo's format from its first bytes, or None if it isn't one Claude can read.

    The type a browser declares comes from the file name, so a PNG screenshot saved as
    `label.jpg` arrives as `image/jpeg`; only the bytes are trusted.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True, slots=True)
class KnownModel:
    """A popular device model. Figures left as None fall back to the catalog's typical values."""

    aliases: tuple[str, ...]
    kind: DeviceKind
    label: str
    draw_watts: float | None = None
    internal_battery_wh: float | None = None
    note: str = ""

    @property
    def has_figures(self) -> bool:
        """Whether we checked this model's own figures (rather than its kind's typical ones)."""
        return self.draw_watts is not None or self.internal_battery_wh is not None


# Figures only where the maker (or a matching battery spec) publishes them; see catalog.py.
# Where the draw depends on a setting the app can't know, we use the highest setting, so the
# runtime shown is never longer than the device really lasts, and the note says so. The planner
# counts 90% of a battery as usable, hence the "x 0.9" below.
KNOWN_MODELS: tuple[KnownModel, ...] = (
    # Inogen One G5 technical manual (96-09302-00-01): the standard 8-cell pack (BA-500, about
    # 92 Wh) runs the G5 "from 1 to 5.5 hours" depending on the setting; the spec sheet's
    # 6.5 hours is on setting 1. 83 W makes 92 Wh last 1 hour (92 x 0.9 / 83).
    KnownModel(
        ("inogen one g5", "inogen g5"),
        "oxygen_concentrator_portable",
        "Inogen One G5",
        83,
        92,
        "These figures assume the highest setting (6): Inogen's manual gives the single battery "
        "(about 92 Wh) 1 to 5.5 hours depending on the setting. On a lower setting it lasts "
        "longer; ask your supplier for your setting's figure.",
    ),
    KnownModel(("inogen one g4", "inogen g4"), "oxygen_concentrator_portable", "Inogen One G4"),
    KnownModel(("inogen rove 6", "rove 6"), "oxygen_concentrator_portable", "Inogen Rove 6"),
    # Inogen's energy savings calculator: 100 W at 2 L/min and 275 W at the highest flow
    # setting, 5 L/min.
    KnownModel(
        ("inogen at home",),
        "oxygen_concentrator_home",
        "Inogen At Home",
        275,
        0,
        "Inogen lists 275 W at its highest setting (5 L/min) and 100 W at 2 L/min; these figures "
        "assume the highest. It has no battery.",
    ),
    KnownModel(
        ("everflo", "philips everflo", "respironics everflo"),
        "oxygen_concentrator_home",
        "Philips EverFlo",
        350,
        0,
        "Philips lists an average power consumption of 350 W and no battery.",
    ),
    KnownModel(("simplygo mini",), "oxygen_concentrator_portable", "Philips SimplyGo Mini"),
    # Philips SimplyGo spec sheet: 14.4 V, 6.6 Ah battery (about 95 Wh) lasting 0.7 h on
    # 2 L/min continuous flow (3.5 h on pulse setting 2); 120 W on AC when not charging.
    # 125 W keeps one battery under 0.7 h (95 x 0.9 / 125 = 0.68).
    KnownModel(
        ("simplygo", "philips simplygo"),
        "oxygen_concentrator_portable",
        "Philips SimplyGo",
        125,
        95,
        "Philips rates one battery (about 95 Wh) at 0.7 hours on 2 L/min continuous flow and "
        "3.5 hours on pulse setting 2; these figures assume continuous flow.",
    ),
    KnownModel(
        ("trilogy evo",),
        "home_ventilator",
        "Philips Trilogy Evo",
        note="Philips rates each of its two batteries (internal and detachable) at about 7.5 "
        "hours.",
    ),
    # Philips Trilogy 100/200: internal and detachable batteries are 14.4 V, 6.8 Ah (about
    # 98 Wh). Philips' Trilogy accessory guide: a detachable battery supplies "at least three
    # hours" under normal use (the user guide says about 4 hours each). 30 W makes 98 Wh last
    # just under 3 hours (98 x 0.9 / 30 = 2.94).
    KnownModel(
        ("trilogy 100", "trilogy 200", "trilogy 202", "trilogy"),
        "home_ventilator",
        "Philips Trilogy",
        30,
        98,
        "Philips says each battery (internal and detachable, about 98 Wh) lasts at least 3 hours "
        "in normal use; these figures assume 3 hours. Your settings change this.",
    ),
    KnownModel(
        ("astral 150", "astral 100", "resmed astral", "astral"),
        "home_ventilator",
        "ResMed Astral",
        20,
        95,
        "ResMed lists a 95 Wh internal battery good for about 8 hours.",
    ),
    KnownModel(
        ("airsense 11",),
        "cpap_bipap",
        "ResMed AirSense 11",
        40,
        0,
        "ResMed measured the AirSense 10 at 34 W with humidifier and heated tube and 7 W "
        "without; we use the same for the 11.",
    ),
    KnownModel(
        ("airsense 10",),
        "cpap_bipap",
        "ResMed AirSense 10",
        40,
        0,
        "ResMed measured 34 W with humidifier and heated tube, 7 W without.",
    ),
    KnownModel(("aircurve",), "cpap_bipap", "ResMed AirCurve"),
    KnownModel(("dreamstation",), "cpap_bipap", "Philips DreamStation"),
    KnownModel(
        ("kangaroo joey", "joey pump"),
        "enteral_feeding_pump",
        "Kangaroo Joey",
        0.75,
        10,
        "Its battery lasts at least 18 hours at 125 mL/h.",
    ),
    KnownModel(("kangaroo epump", "kangaroo e pump"), "enteral_feeding_pump", "Kangaroo ePump"),
    KnownModel(("cadd solis", "cadd legacy", "cadd"), "infusion_pump", "CADD infusion pump"),
    KnownModel(("vacu aide", "vacuaide"), "suction_machine", "DeVilbiss Vacu-Aide"),
    KnownModel(
        ("innospire",),
        "nebulizer",
        "Philips InnoSpire",
        150,
        0,
        "Philips lists 120 V at 1.7 A; treatments take a few minutes.",
    ),
    KnownModel(("permobil",), "power_wheelchair", "Permobil power wheelchair"),
    KnownModel(("jazzy",), "power_wheelchair", "Pride Jazzy power wheelchair"),
    KnownModel(("nxstage",), "home_dialysis", "NxStage home dialysis"),
    KnownModel(("homechoice", "amia", "claria"), "home_dialysis", "Baxter PD cycler"),
)


@dataclass(frozen=True, slots=True)
class PlausibleRange:
    """What one kind of device can realistically draw and hold, to check Claude's figures."""

    min_draw_watts: float
    max_draw_watts: float
    max_battery_wh: float


# A figure outside these is far more likely a misreading (a unit mix-up such as 6800 mAh read
# as 6800 Wh, a guess, or text on a photographed label telling the model what to say) than a
# real device. The minimum draw and the maximum battery guard the dangerous direction: a
# runtime that looks longer than it is. Where nothing better is published, the minimum is half
# the catalog's typical draw.
PLAUSIBLE_RANGES: dict[DeviceKind, PlausibleRange] = {
    # Inogen At Home lists 100 W at 2 L/min, the most frugal we know; 10 L/min units use about
    # 600 W. Stationary units have no battery: one reported is probably a portable's.
    "oxygen_concentrator_home": PlausibleRange(90, 700, 0),
    # Inogen One G5 on setting 1 averages about 14 W (92 Wh in 6.5 h); SimplyGo continuous
    # flow about 120 W. Double battery packs hold up to about 190 Wh.
    "oxygen_concentrator_portable": PlausibleRange(15, 150, 200),
    # ResMed Astral averages about 12 W (95 Wh in 8 h). Internal batteries hold about 100 Wh;
    # a few models carry two.
    "home_ventilator": PlausibleRange(10, 150, 200),
    # ResMed AirSense 10: 22 W with humidifier (7 W without it, which is the saver setting).
    "cpap_bipap": PlausibleRange(20, 200, 100),
    # Portable aspirators carry about an hour of battery at roughly 100 W (UNFPA spec).
    "suction_machine": PlausibleRange(50, 400, 150),
    # Kangaroo Joey: at least 18 h on a small battery, about 0.55 W.
    "enteral_feeding_pump": PlausibleRange(0.5, 10, 25),
    "infusion_pump": PlausibleRange(1, 50, 40),
    # Full-size chairs carry two 12 V batteries, up to about 1.7 kWh.
    "power_wheelchair": PlausibleRange(75, 1000, 2000),
    # Compressor nebulizers; battery mesh nebulizers use a few watts, and counting one at the
    # typical figure only makes the warnings come earlier.
    "nebulizer": PlausibleRange(50, 300, 50),
    "medication_fridge": PlausibleRange(30, 300, 100),
    "home_dialysis": PlausibleRange(100, 2000, 100),
    "pulse_oximeter_monitor": PlausibleRange(2.5, 50, 50),
    # The smallest alternating-pressure pump we found lists 8.5 W.
    "pressure_mattress_pump": PlausibleRange(7.5, 150, 50),
    # Anything else: only the API's own limit on draw. Device batteries over 100 Wh are rare
    # (it is the airline limit); bigger packs belong under backups.
    "custom": PlausibleRange(1, 10_000, 100),
}


def _normalize(text: str) -> str:
    """Lower-case, with any run of non-alphanumerics as one space (`Vacu-Aide` -> `vacu aide`)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _known_model(text: str) -> KnownModel | None:
    """The known model the text names (longest alias wins: "trilogy evo" over "trilogy")."""
    normalized = _normalize(text)
    named = [
        (len(alias), model)
        for model in KNOWN_MODELS
        for alias in model.aliases
        if _contains_phrase(normalized, alias)
    ]
    return max(named, key=lambda item: item[0])[1] if named else None


def _model_with_figures(text: str | None) -> KnownModel | None:
    """The known model the text names, if we have checked figures for it."""
    model = _known_model(text) if text else None
    return model if model is not None and model.has_figures else None


def _match_from_model(model: KnownModel) -> DeviceMatch:
    spec = device_spec(model.kind)
    return DeviceMatch(
        kind=model.kind,
        label=model.label,
        draw_watts=model.draw_watts if model.draw_watts is not None else spec.typical_draw_watts,
        internal_battery_wh=(
            model.internal_battery_wh
            if model.internal_battery_wh is not None
            else spec.internal_battery_wh
        ),
    )


def _from_model(
    model: KnownModel, how: str, confidence: Confidence, source: Literal["ai", "catalog"]
) -> IdentifyDeviceResponse:
    figures = model.note or (
        f"Figures are typical for a {device_spec(model.kind).name.lower()}; check its manual."
    )
    return IdentifyDeviceResponse(
        match=_match_from_model(model),
        confidence=confidence,
        source=source,
        explanation=f"{how} {model.label}. {figures}",
    )


def identify_from_catalog(text: str) -> IdentifyDeviceResponse:
    """Keyword and fuzzy matching against popular models and the catalog (no AI needed)."""
    # 1. A known model named exactly.
    if (model := _known_model(text)) is not None:
        return _from_model(model, "Matched", "high", "catalog")

    # 2. A known model with a typo ("inogn one g5"): compare word windows of the same length.
    words = _normalize(text).split()
    best_ratio, best_model = 0.0, None
    for model in KNOWN_MODELS:
        for alias in model.aliases:
            size = len(alias.split())
            for start in range(max(1, len(words) - size + 1)):
                window = " ".join(words[start : start + size])
                ratio = difflib.SequenceMatcher(None, window, alias).ratio()
                if ratio > best_ratio:
                    best_ratio, best_model = ratio, model
    if best_model is not None and best_ratio >= 0.82:
        return _from_model(best_model, "Looks like", "medium", "catalog")

    # 3. Words that describe a kind of device ("oxygen machine", "feeding pump").
    normalized = " ".join(words)
    scores: dict[DeviceKind, int] = {}
    for spec in DEVICE_CATALOG.values():
        hits = [kw for kw in spec.keywords if _contains_phrase(normalized, _normalize(kw))]
        if hits:
            # Longer, more specific phrases count for more ("portable oxygen" beats "oxygen").
            scores[spec.kind] = sum(len(_normalize(kw).split()) for kw in hits)
    if scores:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        kind, score = ranked[0]
        tied = len(ranked) > 1 and ranked[1][1] == score
        spec = device_spec(kind)
        confidence: Confidence = "low" if tied else "medium"
        return IdentifyDeviceResponse(
            match=DeviceMatch(
                kind=kind,
                label=spec.name,
                draw_watts=spec.typical_draw_watts,
                internal_battery_wh=spec.internal_battery_wh,
            ),
            confidence=confidence,
            source="catalog",
            explanation=f"Sounds like a {spec.name.lower()}. These are typical figures; check "
            "the label or manual for your model's watts and battery.",
        )

    # 4. Nothing recognisable: a custom device the family can fill in.
    spec = device_spec("custom")
    return IdentifyDeviceResponse(
        match=DeviceMatch(
            kind="custom",
            label=text.strip()[:80] or spec.name,
            draw_watts=spec.typical_draw_watts,
            internal_battery_wh=0,
        ),
        confidence="low",
        source="catalog",
        explanation="We didn't recognise this device. Look on its label for watts (W), or "
        "multiply volts (V) by amps (A), and enter that.",
    )


class _ClaudeAnswer(BaseModel):
    """The JSON shape Claude must answer in (enforced with structured outputs)."""

    kind: DeviceKind
    label: str
    draw_watts: float
    internal_battery_wh: float
    confidence: Confidence
    explanation: str


def _system_prompt() -> str:
    kinds = "\n".join(
        f"- {spec.kind}: {spec.name}. Typical {spec.typical_draw_watts:g} W, "
        f"battery {spec.internal_battery_wh:g} Wh. {spec.description}"
        for spec in DEVICE_CATALOG.values()
    )
    return (
        "You identify home medical equipment for LifeLine Power, an app that estimates how long "
        "a family's medical devices can keep running in a power outage. From a device's name or "
        "a photo of its label, choose the matching kind from the list below, give a short label "
        "(brand and model if you can tell), its typical power draw in watts while running, and "
        "the energy of its built-in battery in watt-hours (0 if it has none).\n\n"
        "Use figures printed on the label or published by the maker when you can. If the label "
        "shows volts and amps, watts are about volts x amps. When unsure, lean towards higher "
        "draw and a smaller battery, because overestimating runtime is dangerous, and say so in "
        "the explanation. Keep the explanation to one or two plain sentences saying what you "
        "based the answer on. If it isn't a medical device or you can't tell, use kind "
        '"custom" with confidence "low".\n\nDevice kinds:\n' + kinds
    )


_UNREADABLE_PHOTO = (
    "We couldn't read that photo. Try a clear, well-lit photo of the label, or type the name "
    "printed on it."
)


class DeviceIdentifier:
    """Identifies devices with Claude when configured, otherwise with catalog matching."""

    def __init__(self, client: anthropic.AsyncAnthropic | None, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def ai_enabled(self) -> bool:
        """Whether Claude is configured (photos need it)."""
        return self._client is not None

    async def identify(
        self, text: str | None, image: bytes | None = None, image_type: str | None = None
    ) -> IdentifyDeviceResponse:
        """Identify from text and/or a label photo. Falls back to catalog matching for text.

        `image_type` is the type the client declared for the photo. It isn't trusted: the
        photo's own bytes say what it is (see `sniff_image_type`).
        """
        del image_type
        text = (text or "").strip() or None
        media_type: ImageMediaType | None = None
        if image is not None:
            media_type = sniff_image_type(image)
            if media_type is None:
                raise bad_request("The photo must be a JPEG, PNG, GIF or WebP image.")
            if len(image) > MAX_IMAGE_BYTES:
                raise bad_request("The photo is too large; please send one under 5 MB.")
        if text is None and image is None:
            raise bad_request("Send the device's name as 'text', or a photo as 'image'.")
        if image is None and (model := _model_with_figures(text)) is not None:
            # We checked this model's figures: Claude could only make them less reliable.
            return _from_model(model, "Matched", "high", "catalog")

        if self._client is not None:
            answer = await self._ask_claude(text, image, media_type)
            if answer is not None:
                return answer
        if text is None:
            raise unavailable(
                "Reading photos isn't available right now. Type the name on the device's "
                "label instead."
            )
        return identify_from_catalog(text)

    async def _ask_claude(
        self, text: str | None, image: bytes | None, media_type: ImageMediaType | None
    ) -> IdentifyDeviceResponse | None:
        """Ask Claude; None if the call fails or the answer can't be used."""
        assert self._client is not None
        content: list[ContentBlockParam] = []
        if image is not None and media_type is not None:
            photo: ImageBlockParam = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image).decode(),
                },
            }
            content.append(photo)
        prompt = f"Device: {text}" if text else "Identify the device from this label photo."
        content.append({"type": "text", "text": prompt})
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=2048,
                system=_system_prompt(),
                messages=[{"role": "user", "content": content}],
                output_config={"effort": "low"},
                output_format=_ClaudeAnswer,
            )
        except anthropic.BadRequestError as exc:
            logger.warning("Claude rejected the device request: %s", exc)
            if image is not None and text is None:
                # Our request is otherwise fixed, so this is the photo (corrupt, unreadable).
                raise bad_request(_UNREADABLE_PHOTO) from exc
            return None
        except (anthropic.AnthropicError, ValidationError) as exc:
            logger.warning("Claude device identification failed: %s", exc)
            return None
        answer = response.parsed_output
        if response.stop_reason == "refusal" or answer is None:
            return None
        return _checked(answer, text)


def _checked(answer: _ClaudeAnswer, text: str | None) -> IdentifyDeviceResponse:
    """Claude's answer with figures we can stand behind.

    If Claude's label (or the family's text) names a model we have checked figures for, of the
    kind Claude chose, those figures are used. Otherwise (including models we only know the
    kind of, where Claude may know better than the kind's typical figures) a draw below the
    kind's plausible minimum becomes the catalog's typical draw, one above its maximum is capped
    there, and a battery bigger than the kind can hold becomes the catalog's typical battery;
    any correction drops the confidence to low, so the family checks the figures.
    """
    candidates = (_model_with_figures(answer.label), _model_with_figures(text))
    model = next((m for m in candidates if m is not None and m.kind == answer.kind), None)
    if model is not None:
        return _from_model(model, "Identified as", answer.confidence, "ai")

    spec = device_spec(answer.kind)
    limits = PLAUSIBLE_RANGES[answer.kind]
    draw = answer.draw_watts
    if not draw >= limits.min_draw_watts:  # also catches NaN
        draw = spec.typical_draw_watts
    elif draw > limits.max_draw_watts:
        draw = limits.max_draw_watts
    battery = answer.internal_battery_wh
    if not 0 <= battery <= limits.max_battery_wh:
        battery = spec.internal_battery_wh

    confidence, explanation = answer.confidence, answer.explanation.strip()
    if (draw, battery) != (answer.draw_watts, answer.internal_battery_wh):
        confidence = "low"
        explanation = (
            f"{explanation} Some figures looked wrong for a {spec.name.lower()}, so safer ones "
            "are shown instead: check the watts and battery on the label or in the manual."
        ).strip()
    return IdentifyDeviceResponse(
        match=DeviceMatch(
            kind=answer.kind,
            label=answer.label.strip()[:80] or spec.name,
            draw_watts=round(draw, 1),
            internal_battery_wh=round(battery, 1),
        ),
        confidence=confidence,
        source="ai",
        explanation=explanation,
    )

"""Identify a medical device from its name or a photo of its label.

With an Anthropic API key, Claude reads the text or photo and answers in a fixed JSON shape,
which we check against the catalog. Without a key (or if the call fails) we fall back to matching
the text against popular models and the catalog's keywords, which handles most typed names.
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
_IMAGE_MEDIA_TYPES: dict[str, ImageMediaType] = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",  # not a registered type, but some browsers send it
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class KnownModel:
    """A popular device model. Figures left as None fall back to the catalog's typical values."""

    aliases: tuple[str, ...]
    kind: DeviceKind
    label: str
    draw_watts: float | None = None
    internal_battery_wh: float | None = None
    note: str = ""


# Figures only where the maker (or a matching battery spec) publishes them; see catalog.py.
KNOWN_MODELS: tuple[KnownModel, ...] = (
    KnownModel(
        ("inogen one g5", "inogen g5"),
        "oxygen_concentrator_portable",
        "Inogen One G5",
        25,
        92,
        "Inogen rates the single battery at up to 6.5 hours on setting 1; single packs hold about "
        "92 Wh.",
    ),
    KnownModel(("inogen one g4", "inogen g4"), "oxygen_concentrator_portable", "Inogen One G4"),
    KnownModel(("inogen rove 6", "rove 6"), "oxygen_concentrator_portable", "Inogen Rove 6"),
    KnownModel(
        ("inogen at home",),
        "oxygen_concentrator_home",
        "Inogen At Home",
        100,
        0,
        "Inogen lists about 100 W on flow setting 2; higher settings use more.",
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
    KnownModel(("simplygo",), "oxygen_concentrator_portable", "Philips SimplyGo"),
    KnownModel(
        ("trilogy evo",),
        "home_ventilator",
        "Philips Trilogy Evo",
        note="Philips rates each of its two batteries (internal and detachable) at about 7.5 "
        "hours.",
    ),
    KnownModel(
        ("trilogy 100", "trilogy 200", "trilogy 202", "trilogy"),
        "home_ventilator",
        "Philips Trilogy",
        20,
        98,
        "Trilogy batteries are 14.4 V, 6.8 Ah (about 98 Wh); the detachable one is a second.",
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


def _normalize(text: str) -> str:
    """Lower-case, with any run of non-alphanumerics as one space (`Vacu-Aide` -> `vacu aide`)."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


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


def _model_explanation(model: KnownModel, how: str) -> str:
    figures = model.note or (
        f"Figures are typical for a {device_spec(model.kind).name.lower()}; check its manual."
    )
    return f"{how} {model.label}. {figures}"


def identify_from_catalog(text: str) -> IdentifyDeviceResponse:
    """Keyword and fuzzy matching against popular models and the catalog (no AI needed)."""
    normalized = _normalize(text)

    # 1. A known model named exactly (longest alias wins: "trilogy evo" over "trilogy").
    exact = [
        (len(alias), model)
        for model in KNOWN_MODELS
        for alias in model.aliases
        if _contains_phrase(normalized, alias)
    ]
    if exact:
        model = max(exact, key=lambda item: item[0])[1]
        return IdentifyDeviceResponse(
            match=_match_from_model(model),
            confidence="high",
            source="catalog",
            explanation=_model_explanation(model, "Matched"),
        )

    # 2. A known model with a typo ("inogn one g5"): compare word windows of the same length.
    words = normalized.split()
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
        return IdentifyDeviceResponse(
            match=_match_from_model(best_model),
            confidence="medium",
            source="catalog",
            explanation=_model_explanation(best_model, "Looks like"),
        )

    # 3. Words that describe a kind of device ("oxygen machine", "feeding pump").
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
        """Identify from text and/or a label photo. Falls back to catalog matching for text."""
        text = (text or "").strip() or None
        media_type: ImageMediaType | None = None
        if image is not None:
            media_type = _IMAGE_MEDIA_TYPES.get((image_type or "").lower())
            if media_type is None:
                raise bad_request("The photo must be a JPEG, PNG, GIF or WebP image.")
            if len(image) > MAX_IMAGE_BYTES:
                raise bad_request("The photo is too large; please send one under 5 MB.")
        if text is None and image is None:
            raise bad_request("Send the device's name as 'text', or a photo as 'image'.")

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
        except (anthropic.AnthropicError, ValidationError) as exc:
            logger.warning("Claude device identification failed: %s", exc)
            return None
        answer = response.parsed_output
        if response.stop_reason == "refusal" or answer is None:
            return None
        return _checked(answer)


def _checked(answer: _ClaudeAnswer) -> IdentifyDeviceResponse:
    """Keep Claude's figures inside sane bounds, using the catalog's when they aren't."""
    spec = device_spec(answer.kind)
    draw = answer.draw_watts if 0 < answer.draw_watts <= 10_000 else spec.typical_draw_watts
    battery = (
        answer.internal_battery_wh
        if 0 <= answer.internal_battery_wh <= 100_000
        else spec.internal_battery_wh
    )
    return IdentifyDeviceResponse(
        match=DeviceMatch(
            kind=answer.kind,
            label=answer.label.strip()[:80] or spec.name,
            draw_watts=round(draw, 1),
            internal_battery_wh=round(battery, 1),
        ),
        confidence=answer.confidence,
        source="ai",
        explanation=answer.explanation.strip(),
    )

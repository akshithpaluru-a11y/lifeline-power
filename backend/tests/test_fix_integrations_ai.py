"""Regression tests for the device helper: Claude's figures are checked, curated figures win,
known-model figures don't overstate runtime, and photos are recognised by their bytes."""

from dataclasses import dataclass
from typing import Any, get_args

import anthropic
import httpx
import httpx2
import pytest

from app.ai import (
    KNOWN_MODELS,
    PLAUSIBLE_RANGES,
    DeviceIdentifier,
    _ClaudeAnswer,
    _match_from_model,
    identify_from_catalog,
    sniff_image_type,
)
from app.catalog import DEVICE_CATALOG, DeviceKind
from app.errors import ApiError
from app.planner import USABLE_BATTERY_FRACTION
from app.schemas import DeviceMatch

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
GIF = b"GIF89a\x01\x00\x01\x00"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "


@dataclass
class FakeResponse:
    parsed_output: _ClaudeAnswer | None
    stop_reason: str = "end_turn"


class FakeMessages:
    def __init__(self, outcome: FakeResponse | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClaude:
    def __init__(self, outcome: FakeResponse | Exception) -> None:
        self.messages = FakeMessages(outcome)


def identifier(outcome: FakeResponse | Exception) -> tuple[DeviceIdentifier, FakeMessages]:
    fake = FakeClaude(outcome)
    return DeviceIdentifier(fake, "claude-test-model"), fake.messages  # type: ignore[arg-type]


def answer(**fields: Any) -> FakeResponse:
    base: dict[str, Any] = {
        "kind": "oxygen_concentrator_portable",
        "label": "Portable oxygen concentrator",
        "draw_watts": 35,
        "internal_battery_wh": 92,
        "confidence": "high",
        "explanation": "The label shows a portable oxygen concentrator.",
    }
    return FakeResponse(_ClaudeAnswer(**{**base, **fields}))


def battery_hours(match: DeviceMatch) -> float:
    """How long the planner would say the device's own battery lasts."""
    return match.internal_battery_wh * USABLE_BATTERY_FRACTION / match.draw_watts


# --- Finding 25: Claude's figures checked against the kind and the curated models ------------


async def test_unit_mix_up_on_a_label_does_not_become_a_huge_battery() -> None:
    # "14.4V 6800mAh" read as 6800 Wh: about 245 h of runtime instead of about 3.
    helper, _ = identifier(answer(draw_watts=25, internal_battery_wh=6800))
    result = await helper.identify(None, JPEG, "image/jpeg")
    assert result.source == "ai"
    assert (
        result.match.internal_battery_wh
        == DEVICE_CATALOG["oxygen_concentrator_portable"].internal_battery_wh
    )
    assert battery_hours(result.match) < 4
    assert result.confidence == "low"
    assert "check" in result.explanation.lower()


async def test_implausibly_low_draw_for_the_kind_is_replaced() -> None:
    helper, _ = identifier(answer(draw_watts=5))
    result = await helper.identify("my oxygen machine")
    assert (
        result.match.draw_watts == DEVICE_CATALOG["oxygen_concentrator_portable"].typical_draw_watts
    )
    assert result.confidence == "low"


async def test_implausibly_high_draw_is_capped_not_lowered_to_typical() -> None:
    helper, _ = identifier(
        answer(
            kind="enteral_feeding_pump",
            label="Feeding pump",
            draw_watts=5000,
            internal_battery_wh=10,
        )
    )
    result = await helper.identify("feeding pump")
    assert result.match.draw_watts == PLAUSIBLE_RANGES["enteral_feeding_pump"].max_draw_watts
    assert result.confidence == "low"


async def test_plausible_answer_for_an_unlisted_model_is_kept() -> None:
    helper, _ = identifier(answer(label="OxyGo Next", draw_watts=40, confidence="medium"))
    result = await helper.identify("OxyGo Next")
    assert result.source == "ai"
    assert result.confidence == "medium"
    assert result.match.model_dump() == {
        "kind": "oxygen_concentrator_portable",
        "label": "OxyGo Next",
        "draw_watts": 40,
        "internal_battery_wh": 92,
    }
    assert result.explanation == "The label shows a portable oxygen concentrator."


async def test_typed_known_model_uses_curated_figures_without_asking_claude() -> None:
    helper, messages = identifier(
        answer(
            kind="home_ventilator",
            label="Philips Trilogy 100",
            draw_watts=20,
            internal_battery_wh=98,
        )
    )
    result = await helper.identify("Trilogy 100")
    assert result == identify_from_catalog("Trilogy 100")
    assert result.confidence == "high"
    assert messages.calls == []


async def test_photo_of_a_known_model_gets_curated_figures() -> None:
    helper, _ = identifier(
        answer(
            kind="home_ventilator",
            label="Philips Trilogy 100",
            draw_watts=20,
            internal_battery_wh=98,
        )
    )
    result = await helper.identify(None, JPEG, "image/jpeg")
    trilogy = identify_from_catalog("Trilogy 100").match
    assert result.source == "ai"
    assert result.match == trilogy
    assert "Trilogy" in result.explanation


async def test_model_we_only_know_the_kind_of_keeps_claudes_checked_figures() -> None:
    # The catalog has no G4 figures of its own (the kind's typical 92 Wh); Claude's smaller,
    # plausible battery is the safer figure, so it is kept.
    helper, messages = identifier(
        answer(label="Inogen One G4", draw_watts=30, internal_battery_wh=46, confidence="high")
    )
    result = await helper.identify("Inogen One G4")
    assert messages.calls
    assert result.source == "ai"
    assert (result.match.draw_watts, result.match.internal_battery_wh) == (30, 46)


async def test_known_model_of_another_kind_is_not_forced_onto_the_answer() -> None:
    # The family typed "astral" but the photo shows a CPAP: keep Claude's (checked) CPAP.
    helper, _ = identifier(
        answer(kind="cpap_bipap", label="ResMed AirSense 10", draw_watts=40, internal_battery_wh=0)
    )
    result = await helper.identify("astral", JPEG, "image/jpeg")
    assert result.match.kind == "cpap_bipap"
    assert result.match.label == "ResMed AirSense 10"


def test_every_kind_has_a_plausible_range_that_holds_the_catalog_figures() -> None:
    assert set(PLAUSIBLE_RANGES) == set(get_args(DeviceKind))
    for kind, limits in PLAUSIBLE_RANGES.items():
        spec = DEVICE_CATALOG[kind]
        assert limits.min_draw_watts <= spec.typical_draw_watts <= limits.max_draw_watts, kind
        assert spec.internal_battery_wh <= limits.max_battery_wh, kind


def test_every_known_model_is_within_its_kinds_range() -> None:
    for model in KNOWN_MODELS:
        match = _match_from_model(model)
        limits = PLAUSIBLE_RANGES[model.kind]
        assert limits.min_draw_watts <= match.draw_watts <= limits.max_draw_watts, model.label
        assert match.internal_battery_wh <= limits.max_battery_wh, model.label


# --- Finding 26: Trilogy 100/200 figures --------------------------------------------------------


@pytest.mark.parametrize("text", ["Trilogy 100", "Trilogy 200", "Philips Trilogy"])
def test_trilogy_battery_runtime_is_not_above_philips_three_hours(text: str) -> None:
    result = identify_from_catalog(text)
    assert result.match.internal_battery_wh == 98
    # Philips: a detachable battery supplies "at least three hours" under normal use.
    assert battery_hours(result.match) <= 3


def test_trilogy_evo_keeps_its_own_entry() -> None:
    assert identify_from_catalog("Trilogy Evo").match.label == "Philips Trilogy Evo"


# --- Finding 42: Inogen figures assume the highest setting ---------------------------------------


def test_inogen_at_home_uses_its_highest_flow_figure() -> None:
    result = identify_from_catalog("Inogen At Home")
    assert result.match.draw_watts == 275  # Inogen: 275 W at 5 L/min
    assert "5 L/min" in result.explanation


def test_inogen_g5_battery_runtime_matches_the_highest_setting() -> None:
    result = identify_from_catalog("Inogen One G5")
    assert result.match.internal_battery_wh == 92
    # Inogen's G5 technical manual: the standard 8-cell pack lasts 1 to 5.5 hours.
    assert battery_hours(result.match) <= 1
    assert "highest setting" in result.explanation


def test_simplygo_continuous_flow_is_not_overstated() -> None:
    result = identify_from_catalog("Philips SimplyGo")
    # Philips: one battery lasts 0.7 h at 2 L/min continuous flow.
    assert battery_hours(result.match) <= 0.7


# --- Finding 33: the photo's type comes from its bytes -----------------------------------------


@pytest.mark.parametrize(
    ("data", "media_type"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (GIF, "image/gif"), (WEBP, "image/webp")],
)
def test_sniff_image_type(data: bytes, media_type: str) -> None:
    assert sniff_image_type(data) == media_type


@pytest.mark.parametrize("data", [b"", b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVEfmt ", b"\xff\xd8"])
def test_sniff_rejects_other_data(data: bytes) -> None:
    assert sniff_image_type(data) is None


async def test_png_saved_as_jpg_is_sent_as_png() -> None:
    helper, messages = identifier(answer())
    await helper.identify(None, PNG, "image/jpeg")
    image = messages.calls[0]["messages"][0]["content"][0]
    assert image["source"]["media_type"] == "image/png"


async def test_photo_that_is_not_an_image_is_a_400_even_with_an_image_type() -> None:
    helper, messages = identifier(answer())
    with pytest.raises(ApiError) as caught:
        await helper.identify(None, b"this is not a photo", "image/jpeg")
    assert caught.value.status_code == 400
    assert messages.calls == []


async def test_endpoint_rejects_non_image_bytes_with_400(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/ai/identify-device", files={"image": ("label.jpg", b"not an image", "image/jpeg")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def _claude_rejected_image() -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        "Could not process image", response=httpx2.Response(400, request=request), body=None
    )


async def test_photo_claude_cannot_read_is_a_400_not_unavailable() -> None:
    helper, _ = identifier(_claude_rejected_image())
    with pytest.raises(ApiError) as caught:
        await helper.identify(None, JPEG, "image/jpeg")
    assert caught.value.status_code == 400
    assert "photo" in caught.value.message.lower()


async def test_photo_claude_cannot_read_falls_back_to_the_text() -> None:
    helper, _ = identifier(_claude_rejected_image())
    result = await helper.identify("feeding pump", JPEG, "image/jpeg")
    assert result.source == "catalog"
    assert result.match.kind == "enteral_feeding_pump"

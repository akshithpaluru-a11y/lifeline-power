"""AI device helper: catalog fallback without a key, and the Claude path with the SDK mocked."""

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from app.ai import DeviceIdentifier, _ClaudeAnswer, identify_from_catalog
from app.errors import ApiError


@pytest.mark.parametrize(
    ("text", "kind", "label", "confidence"),
    [
        ("Inogen One G5", "oxygen_concentrator_portable", "Inogen One G5", "high"),
        (
            "my dad's inogen g5 concentrator",
            "oxygen_concentrator_portable",
            "Inogen One G5",
            "high",
        ),
        ("Trilogy Evo", "home_ventilator", "Philips Trilogy Evo", "high"),
        ("Inogn One G5", "oxygen_concentrator_portable", "Inogen One G5", "medium"),
        ("feeding pump", "enteral_feeding_pump", "Feeding pump", "medium"),
    ],
)
def test_catalog_matching(text: str, kind: str, label: str, confidence: str) -> None:
    result = identify_from_catalog(text)
    assert (result.match.kind, result.match.label, result.confidence) == (kind, label, confidence)
    assert result.source == "catalog"


def test_known_model_figures() -> None:
    result = identify_from_catalog("Inogen One G5")
    assert (result.match.draw_watts, result.match.internal_battery_wh) == (25, 92)
    assert "6.5 hours" in result.explanation


def test_unknown_device_becomes_custom() -> None:
    result = identify_from_catalog("Acme Widget 3000")
    assert result.match.kind == "custom"
    assert result.match.label == "Acme Widget 3000"
    assert result.confidence == "low"


async def test_endpoint_without_key_uses_catalog(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/ai/identify-device", json={"text": "ResMed AirSense 10"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "catalog"
    assert body["match"]["kind"] == "cpap_bipap"
    assert set(body) == {"match", "confidence", "source", "explanation"}
    assert set(body["match"]) == {"kind", "label", "draw_watts", "internal_battery_wh"}


async def test_endpoint_validation(client: httpx.AsyncClient) -> None:
    for body in ({}, {"text": ""}, {"text": "x" * 501}):
        response = await client.post("/api/ai/identify-device", json=body)
        assert response.status_code == 400, body
    response = await client.post(
        "/api/ai/identify-device", content=b"nope", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


async def test_photo_without_key_is_unavailable(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/ai/identify-device", files={"image": ("label.jpg", b"\xff\xd8\xff", "image/jpeg")}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


async def test_photo_with_text_falls_back_to_text(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/ai/identify-device",
        files={"image": ("label.jpg", b"\xff\xd8\xff", "image/jpeg")},
        data={"text": "Astral 150"},
    )
    assert response.status_code == 200
    assert response.json()["match"]["kind"] == "home_ventilator"


async def test_photo_must_be_an_image(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/ai/identify-device", files={"image": ("label.pdf", b"%PDF", "application/pdf")}
    )
    assert response.status_code == 400


# --- With Claude (the SDK client is replaced by a fake) --------------------------------------


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


ANSWER = _ClaudeAnswer(
    kind="oxygen_concentrator_portable",
    label="Inogen One G5",
    draw_watts=28,
    internal_battery_wh=99,
    confidence="high",
    explanation="Label shows Inogen One G5; 8-cell battery is about 99 Wh.",
)


async def test_claude_answer_is_used() -> None:
    helper, messages = identifier(FakeResponse(ANSWER))
    assert helper.ai_enabled
    result = await helper.identify("Inogen One G5")
    assert result.source == "ai"
    assert result.match.model_dump() == {
        "kind": "oxygen_concentrator_portable",
        "label": "Inogen One G5",
        "draw_watts": 28,
        "internal_battery_wh": 99,
    }
    call = messages.calls[0]
    assert call["model"] == "claude-test-model"
    assert call["output_format"] is _ClaudeAnswer
    assert call["messages"][0]["content"][-1] == {"type": "text", "text": "Device: Inogen One G5"}


async def test_photo_is_sent_as_an_image_block() -> None:
    helper, messages = identifier(FakeResponse(ANSWER))
    await helper.identify(None, b"\x89PNG", "image/png")
    image = messages.calls[0]["messages"][0]["content"][0]
    assert image["type"] == "image"
    assert image["source"] == {"type": "base64", "media_type": "image/png", "data": "iVBORw=="}


async def test_out_of_range_figures_are_replaced_with_catalog_values() -> None:
    wild = ANSWER.model_copy(update={"draw_watts": -5, "internal_battery_wh": 1e9})
    helper, _ = identifier(FakeResponse(wild))
    result = await helper.identify("Inogen One G5")
    assert (result.match.draw_watts, result.match.internal_battery_wh) == (30, 92)


@pytest.mark.parametrize(
    "outcome",
    [
        FakeResponse(None, stop_reason="refusal"),
        FakeResponse(None),
        anthropic.AnthropicError("network down"),
    ],
)
async def test_falls_back_to_catalog_when_claude_fails(outcome: FakeResponse | Exception) -> None:
    helper, _ = identifier(outcome)
    result = await helper.identify("Inogen One G5")
    assert result.source == "catalog"
    assert result.match.label == "Inogen One G5"


async def test_photo_only_fails_cleanly_when_claude_fails() -> None:
    helper, _ = identifier(FakeResponse(None, stop_reason="refusal"))
    with pytest.raises(ApiError) as caught:
        await helper.identify(None, b"\xff\xd8", "image/jpeg")
    assert caught.value.status_code == 503

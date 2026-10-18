"""Serving the built web app from the API's origin (`LIFELINE_STATIC_DIR`)."""

from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.fixture
def web_root(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>LifeLine</title>")
    (root / "assets" / "app.js").write_text("console.log('hi')")
    (tmp_path / "secret.txt").write_text("not public")
    return root


async def test_web_app_and_api_share_one_origin(settings: Settings, web_root: Path) -> None:
    app = create_app(settings.model_copy(update={"static_dir": web_root}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert "LifeLine" in (await client.get("/")).text
        assert "LifeLine" in (await client.get("/live")).text  # the app's own routes
        assert (await client.get("/assets/app.js")).text == "console.log('hi')"
        assert "not public" not in (await client.get("/../secret.txt")).text
        assert "not public" not in (await client.get("/assets/..%2F..%2Fsecret.txt")).text
        missing = await client.get("/api/nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        catalog = await client.get("/api/catalog/devices")
        assert catalog.status_code == 200
        head = await client.head("/assets/app.js")  # link checkers and some proxies use HEAD
        assert head.status_code == 200


@pytest.mark.parametrize(
    "path", ["/a%00b", "/" + "x" * 300, "/assets/" + "y" * 5000], ids=["nul", "long", "longer"]
)
async def test_odd_paths_get_the_web_app_not_an_error(
    settings: Settings, web_root: Path, path: str
) -> None:
    app = create_app(settings.model_copy(update={"static_dir": web_root}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
        assert response.status_code == 200
        assert "LifeLine" in response.text


def test_a_web_app_folder_without_index_is_refused(settings: Settings, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        create_app(settings.model_copy(update={"static_dir": tmp_path}))

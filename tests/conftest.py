from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path):
    base = load_settings(tmp_path / "data")
    return replace(base, demo=True, seed=False, api_key="", max_review_rounds=3, target_chars=1200)


@pytest.fixture()
def client(settings):
    app = create_app(settings, seed=False)
    with TestClient(app) as test_client:
        yield test_client


def create_project(client: TestClient, title: str = "测试存款分层", **extra: Any) -> str:
    response = client.post(
        "/api/projects",
        json={"title": title, "topic": extra.pop("topic", "存款分层"), "targetChars": extra.pop("targetChars", 1200), **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()["project"]["slug"]


def add_text(client: TestClient, slug: str, text: str | None = None, title: str = "素材一") -> dict[str, Any]:
    response = client.post(
        f"/api/projects/{slug}/materials/text",
        json={
            "text": text
            or "# 素材一\n\n[¶1] 很多人把闲钱一次性存三年定期，因为期限越长利率越高。\n\n"
            "[¶2] 现在部分期限出现倒挂，五年期不一定比三年期高。\n\n"
            "[¶3] 先问这笔钱什么时候要用，再决定期限。\n\n"
            "[¶4] 应急金要留在随时能取的地方。",
            "title": title,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


def wait_idle(client: TestClient, slug: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    state: dict[str, Any] = {}
    while time.time() < deadline:
        state = client.get(f"/api/projects/{slug}/state").json()
        if not state.get("running"):
            return state
        time.sleep(0.04)
    raise AssertionError(f"pipeline did not settle: {state}")


def resume(client: TestClient, slug: str, payload: dict[str, Any]) -> None:
    response = client.post(f"/api/projects/{slug}/resume", json=payload)
    assert response.status_code == 200, response.text

"""Tests for system and configuration routes."""

from __future__ import annotations

import re

import httpx
import pytest
import yaml

from aistudio_api.api.app import app
from aistudio_api.config import resolve_config_file
from aistudio_api.infrastructure.gateway.model_defaults import (
    get_configured_request_timeouts,
    invalidate_config_cache,
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.mark.asyncio
async def test_health_endpoint():
    async with _client() as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_check_endpoint():
    async with _client() as client:
        resp = await client.get("/auth/check")
        assert resp.status_code == 200
        data = resp.json()
        assert "auth_enabled" in data


@pytest.mark.asyncio
async def test_spa_endpoints():
    import asyncio

    async with _client() as client:
        responses = await asyncio.gather(
            *[
                client.get(path, headers={"Accept": "text/html"})
                for path in ("/", "/login", "/accounts", "/settings")
            ]
        )
        for resp in responses:
            assert resp.status_code == 200
            assert "<!DOCTYPE html>" in resp.text
            assert "Accept" in resp.headers.get("Vary", "")
            assert "no-store" in resp.headers.get("Cache-Control", "")


@pytest.mark.asyncio
async def test_spa_html_references_versioned_assets():
    """浏览器重新打开页面时必须加载与当前构建对应的 JS，避免继续运行旧设置页。"""
    async with _client() as client:
        response = await client.get("/settings", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert "no-store" in response.headers["cache-control"]
        scripts = re.findall(r'src="(/assets/index-[A-Za-z0-9_-]+\.js)"', response.text)
        assert len(scripts) == 1
        script = await client.get(scripts[0])
        assert script.status_code == 200
        assert "/config/timeouts" in script.text


@pytest.mark.asyncio
async def test_get_config_endpoint():
    async with _client() as client:
        resp = await client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "port" in data
        assert "browser_port" in data
        assert "yaml_content" in data


@pytest.mark.asyncio
async def test_update_config_yaml_validation():
    async with _client() as client:
        resp = await client.put(
            "/config/yaml", json={"yaml_content": "invalid: [yaml: broken"}
        )
        assert resp.status_code == 400

        resp_non_dict = await client.put(
            "/config/yaml", json={"yaml_content": "- item1\n- item2"}
        )
        assert resp_non_dict.status_code == 400


@pytest.mark.asyncio
async def test_timeout_settings_persist_and_preserve_other_config():
    path = resolve_config_file()
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    values = {"timeout_stream": 600, "timeout_replay": 300}
    try:
        async with _client() as client:
            response = await client.put("/config/timeouts", json=values)
            assert response.status_code == 200
            assert response.json() == {"ok": True, **values}
            config = (await client.get("/config")).json()
            assert {key: config[key] for key in values} == values
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert saved.pop("request_timeouts") == values
        assert saved == before
        invalidate_config_cache()
        assert get_configured_request_timeouts().model_dump() == values
    finally:
        path.write_text(yaml.safe_dump(before), encoding="utf-8")
        invalidate_config_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, 86401, 1.5, True, "600", None])
async def test_timeout_settings_reject_invalid_values_without_writing(value):
    path = resolve_config_file()
    before = path.read_text(encoding="utf-8")
    async with _client() as client:
        response = await client.put(
            "/config/timeouts", json={"timeout_stream": value, "timeout_replay": 120}
        )
        assert response.status_code == 400
    assert path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_yaml_timeout_settings_reload_and_validate():
    path = resolve_config_file()
    before = path.read_text(encoding="utf-8")
    try:
        async with _client() as client:
            values = {"timeout_stream": 240, "timeout_replay": 480}
            config = yaml.safe_load(before)
            config["request_timeouts"] = values
            response = await client.put(
                "/config/yaml", json={"yaml_content": yaml.safe_dump(config)}
            )
            assert response.status_code == 200
            assert get_configured_request_timeouts().model_dump() == values
            valid_content = path.read_text(encoding="utf-8")
            for invalid in ({"timeout_stream": 0}, [], None):
                config["request_timeouts"] = invalid
                response = await client.put(
                    "/config/yaml", json={"yaml_content": yaml.safe_dump(config)}
                )
                assert response.status_code == 400
                assert path.read_text(encoding="utf-8") == valid_content
    finally:
        path.write_text(before, encoding="utf-8")
        invalidate_config_cache()


@pytest.mark.asyncio
async def test_rotation_status_and_clear_cooldown():
    from unittest.mock import MagicMock

    from aistudio_api.api.state import runtime_state
    from aistudio_api.application.account_rotator import AccountRotator

    mock_rotator = MagicMock(spec=AccountRotator)
    mock_rotator.get_all_stats.return_value = {"acc_1": {"requests": 1}}

    runtime_state.rotator = mock_rotator
    async with _client() as client:
        resp = await client.get("/rotation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "sticky"
        assert "accounts" in data

        resp_clear = await client.post(
            "/rotation/clear-cooldown", json={"account_id": "acc_1"}
        )
        assert resp_clear.status_code == 200
        mock_rotator.clear_cooldown.assert_called_once_with("acc_1", model=None)

"""Generation deadlines terminate work without changing account health or selection."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from aistudio_api.api.schemas import (
    GeminiContent,
    GeminiGenerateContentRequest,
    GeminiPart,
)
from aistudio_api.api.state import runtime_state
from aistudio_api.application import api_service_gemini as service
from aistudio_api.domain.errors import RequestError
from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.browser.scripts import (
    STREAM_CLEANUP_JS,
    STREAMING_INIT_JS,
)
from aistudio_api.infrastructure.gateway.client import AIStudioClient
from aistudio_api.infrastructure.gateway.model_defaults import RequestTimeoutConfig
from aistudio_api.infrastructure.gateway.transport import XHRStreamTransport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["browser", "python_no_status", "python_with_status"]
)
async def test_stream_deadlines_return_504_and_cleanup(monkeypatch, source):
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    page.add_binding = AsyncMock()
    listener = MagicMock()

    def on_binding(name, callback):
        nonlocal listener
        listener = callback

    page.on_binding.side_effect = on_binding

    async def evaluate(script, args=None):
        if script == STREAMING_INIT_JS:
            assert isinstance(args, dict)
            if source != "python_no_status":
                listener(
                    json.dumps({"rid": args["rid"], "type": "status", "status": 200})
                )
            if source == "browser":
                listener(
                    json.dumps(
                        {"rid": args["rid"], "type": "error", "message": "timeout"}
                    )
                )

    page.evaluate = AsyncMock(side_effect=evaluate)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.transport._ensure_authorization_header",
        AsyncMock(return_value={}),
    )
    with pytest.raises(RequestError) as caught:
        async for _ in transport.send_streaming_request(
            page, url="https://example.test", headers={}, body="{}", timeout_ms=30
        ):
            pass
    assert caught.value.status == 504
    assert transport.in_flight_count == 0
    assert page.evaluate.await_args_list[-1].args[0] == STREAM_CLEANUP_JS


@pytest.mark.asyncio
async def test_replay_deadline_returns_504(monkeypatch):
    page = MagicMock(spec=CDPPage)
    page.evaluate = AsyncMock(return_value={"status": 0, "body": "timeout"})
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.transport._ensure_authorization_header",
        AsyncMock(return_value={}),
    )
    with pytest.raises(RequestError) as caught:
        await XHRStreamTransport().send_hooked_request(
            page, url="https://example.test", headers={}, body="{}", timeout_ms=30
        )
    assert caught.value.status == 504


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_python_cancellation_aborts_browser_request(monkeypatch, streaming):
    transport = XHRStreamTransport()
    page = MagicMock(spec=CDPPage)
    page.is_closed.return_value = False
    page.add_binding = AsyncMock()
    started = asyncio.Event()
    rid = None

    async def evaluate(script, args=None, **kwargs):
        nonlocal rid
        if script == STREAM_CLEANUP_JS:
            assert args == rid
            return
        assert isinstance(args, dict)
        rid = args["rid"]
        started.set()
        if not streaming:
            await asyncio.Event().wait()

    page.evaluate = AsyncMock(side_effect=evaluate)
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.transport._ensure_authorization_header",
        AsyncMock(return_value={}),
    )

    async def run():
        args = {
            "url": "https://example.test",
            "headers": {},
            "body": "{}",
            "timeout_ms": 5000,
        }
        if streaming:
            async for _ in transport.send_streaming_request(page, **args):
                pass
        else:
            await transport.send_hooked_request(page, **args)

    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.in_flight_count == 0
    assert page.evaluate.await_args_list[-1].args == (STREAM_CLEANUP_JS, rid)


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize(
    "error", [TimeoutError(), RequestError(504, "Generation timed out")]
)
async def test_stream_timeout_emits_error_without_retry_or_account_mutation(
    monkeypatch, partial, error
):
    switch = AsyncMock(return_value=True)
    record = MagicMock()
    monkeypatch.setattr(service, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(service, "try_switch_account", switch)
    monkeypatch.setattr(service, "record_rotator_event", record)
    runtime_record = MagicMock()
    monkeypatch.setattr(runtime_state, "record", runtime_record)
    client = MagicMock(spec=AIStudioClient)

    async def stream(**kwargs):
        if partial:
            yield "body", "partial output"
        raise error

    client.stream_generate_content.side_effect = stream
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    response = service._build_gemini_streaming_response(
        client=client, req=req, model_path="gemini-4-argon"
    )
    chunks = [str(chunk) async for chunk in response.body_iterator]
    payload = json.loads(chunks[-1].removeprefix("data: "))
    assert payload["error"]["code"] == 504
    assert payload["error"]["status"] == "DEADLINE_EXCEEDED"
    assert all("usageMetadata" not in chunk for chunk in chunks)
    client.stream_generate_content.assert_called_once()
    client.clear_templates.assert_not_called()
    switch.assert_not_awaited()
    record.assert_not_called()
    runtime_record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError(), RequestError(504, "Generation timed out")]
)
async def test_nonstream_timeout_returns_504_without_account_changes(
    monkeypatch, error
):
    switch = AsyncMock(return_value=True)
    record = MagicMock()
    monkeypatch.setattr(service, "ensure_active_account", AsyncMock())
    monkeypatch.setattr(service, "try_switch_account", switch)
    monkeypatch.setattr(service, "record_rotator_event", record)
    client = MagicMock(spec=AIStudioClient)
    client.generate_content = AsyncMock(side_effect=error)
    req = GeminiGenerateContentRequest(
        contents=[GeminiContent(role="user", parts=[GeminiPart(text="hello")])]
    )
    with pytest.raises(HTTPException) as caught:
        await service.handle_gemini_generate_content(
            "gemini-4-argon", req, client, stream=False
        )
    assert caught.value.status_code == 504
    assert service.classify_gemini_error_payload(caught.value)[2] == "DEADLINE_EXCEEDED"
    client.generate_content.assert_awaited_once()
    client.clear_templates.assert_not_called()
    switch.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_total_generation_deadline_cancels_underlying_work(monkeypatch, stream):
    client = AIStudioClient()
    cancelled = asyncio.Event()
    monkeypatch.setattr(
        "aistudio_api.infrastructure.gateway.client.get_configured_request_timeouts",
        lambda: RequestTimeoutConfig.model_construct(
            timeout_stream=0.03, timeout_replay=0.03
        ),
    )

    async def capture(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(client, "capture_request", capture)
    with pytest.raises(TimeoutError):
        if stream:
            async for _ in client.stream_generate_content(capture_prompt="hello"):
                pass
        else:
            await client.generate_content(capture_prompt="hello")
    assert cancelled.is_set()
    assert client.session._in_flight == 0

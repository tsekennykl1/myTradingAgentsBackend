"""Fast unit checks for the public MCP wrappers (no LLM keys or network)."""

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import mcp_server


def test_public_mcp_has_expected_tools():
    names = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}
    assert mcp_server.ALL_TOOLS <= names


def test_invalid_run_id_is_rejected():
    with pytest.raises(ToolError, match="Invalid run id"):
        mcp_server.get_analysis("../../.env")


def test_invalid_report_name_is_rejected():
    with pytest.raises(ToolError, match="Invalid report name"):
        mcp_server._report_name("../secret")


def test_disabled_mcp_blocks_tools(monkeypatch):
    monkeypatch.setenv("MCP_ENABLED", "0")
    with pytest.raises(ToolError, match="disabled"):
        mcp_server.get_engine_info()


def test_ai_model_catalogue_never_returns_env_keys(monkeypatch):
    monkeypatch.setattr(
        "app.routes.providers.list_providers",
        lambda: {
            "active": {"provider": "deepseek", "configured": True},
            "providers": [{
                "provider": "deepseek", "label": "DeepSeek", "configured": True,
                "supports_balance": True, "env_keys": ["DEEPSEEK_API_KEY"],
                "deep_models": ["deep"], "quick_models": ["quick"],
                "default_deep_model": "deep", "default_quick_model": "quick",
            }],
        },
    )
    payload = mcp_server.list_ai_models()
    assert "env_keys" not in json.dumps(payload)
    assert "API_KEY" not in json.dumps(payload)


def test_list_analyses_is_bounded(monkeypatch):
    monkeypatch.setattr(
        "app.services.analyze_service.list_runs",
        lambda: [{"run_id": str(i), "status": "completed", "payload": {"ticker": "AAPL"}} for i in range(5)],
    )
    assert mcp_server.list_analyses(limit=2)["count"] == 2
    with pytest.raises(ToolError, match="between 1 and 100"):
        mcp_server.list_analyses(limit=101)


def _scope(headers):
    return {"type": "http", "headers": headers, "method": "POST", "path": "/mcp"}


async def _collect(middleware, headers):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(_scope(headers), receive, send)
    return sent


def test_mcp_rejects_missing_access_key(monkeypatch):
    import asyncio

    from app.mcp_server import AccessKeyMiddleware

    monkeypatch.setenv("MCP_ACCESS_KEY", "s3cret")
    called = []

    async def inner(scope, receive, send):
        called.append(scope)

    sent = asyncio.run(_collect(AccessKeyMiddleware(inner), []))
    assert sent[0]["status"] == 401
    assert not called


def test_mcp_accepts_valid_access_key(monkeypatch):
    import asyncio

    from app.mcp_server import AccessKeyMiddleware

    monkeypatch.setenv("MCP_ACCESS_KEY", "s3cret")
    called = []

    async def inner(scope, receive, send):
        called.append(scope)

    asyncio.run(_collect(AccessKeyMiddleware(inner), [(b"authorization", b"Bearer s3cret")]))
    assert called


def test_mcp_closed_when_key_unset(monkeypatch):
    import asyncio

    from app.mcp_server import AccessKeyMiddleware

    monkeypatch.delenv("MCP_ACCESS_KEY", raising=False)
    called = []

    async def inner(scope, receive, send):
        called.append(scope)

    sent = asyncio.run(_collect(AccessKeyMiddleware(inner), [(b"x-mcp-key", b"anything")]))
    assert sent[0]["status"] == 401
    assert not called

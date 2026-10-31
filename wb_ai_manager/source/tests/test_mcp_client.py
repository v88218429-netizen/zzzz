import asyncio
import sys
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from wb_control_center.mcp_client import WBMCPClient, WBMCPError


def install_fake_mcp(monkeypatch, events, *, block_initialize=False):
    @asynccontextmanager
    async def fake_stdio_client(_params):
        events["stdio_entries"] = events.get("stdio_entries", 0) + 1
        events.setdefault("entered_tasks", []).append(asyncio.current_task())
        try:
            yield ("read-stream", "write-stream")
        finally:
            events.setdefault("exited_tasks", []).append(asyncio.current_task())

    class FakeSession:
        def __init__(self, _read, _write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            events.setdefault("session_exited_tasks", []).append(asyncio.current_task())

        async def initialize(self):
            if block_initialize:
                await asyncio.Event().wait()

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="echo")])

        async def call_tool(self, _tool, arguments=None):
            return SimpleNamespace(structured_content={"arguments": arguments})

    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    mcp.StdioServerParameters = lambda **kwargs: SimpleNamespace(**kwargs)
    client_pkg = types.ModuleType("mcp.client")
    client_pkg.__path__ = []
    stdio_pkg = types.ModuleType("mcp.client.stdio")
    stdio_pkg.stdio_client = fake_stdio_client
    mcp.client = client_pkg
    client_pkg.stdio = stdio_pkg
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client_pkg)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_pkg)


@pytest.mark.asyncio
async def test_concurrent_start_uses_one_owner_and_closes_stdio_in_that_task(monkeypatch):
    events = {}
    install_fake_mcp(monkeypatch, events)
    client = WBMCPClient("seller-token", "/tmp/wb-mcp", start_timeout=1)

    await asyncio.gather(*(client.start() for _ in range(8)))
    assert events["stdio_entries"] == 1
    assert await client.call("echo", {"value": 7}) == {"arguments": {"value": 7}}

    await asyncio.create_task(client.close())
    assert len(events["entered_tasks"]) == 1
    assert events["entered_tasks"] == events["exited_tasks"]
    assert events["entered_tasks"] == events["session_exited_tasks"]
    assert events["entered_tasks"][0] is not asyncio.current_task()
    assert client.session is None


@pytest.mark.asyncio
async def test_start_timeout_cancels_owner_and_unwinds_context_in_owner_task(monkeypatch):
    events = {}
    install_fake_mcp(monkeypatch, events, block_initialize=True)
    client = WBMCPClient("seller-token", "/tmp/wb-mcp", start_timeout=0.02)

    with pytest.raises(WBMCPError, match="startup timed out"):
        await client.start()

    assert events["entered_tasks"] == events["exited_tasks"]
    assert events["entered_tasks"] == events["session_exited_tasks"]
    assert client.session is None
    assert client._owner_task is None


@pytest.mark.asyncio
async def test_tool_timeout_resets_transport_without_cross_task_context_exit(monkeypatch):
    events = {}
    install_fake_mcp(monkeypatch, events)

    class SlowSession:
        def __init__(self, _read, _write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            events.setdefault("session_exited_tasks", []).append(asyncio.current_task())

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name="slow")])

        async def call_tool(self, _tool, arguments=None):
            await asyncio.Event().wait()

    # Replace only the session class; the fake stdio context still records owner task identity.
    monkeypatch.setitem(sys.modules["mcp"], "ClientSession", SlowSession)
    client = WBMCPClient("seller-token", "/tmp/wb-mcp", start_timeout=1, call_timeout=0.02)

    with pytest.raises(WBMCPError, match="timed out"):
        await client.call("slow")

    assert events["entered_tasks"] == events["exited_tasks"]
    assert events["entered_tasks"] == events["session_exited_tasks"]
    assert client.session is None

import sys
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from escanor import config
from escanor.app import build_app, build_model
from escanor.connectors.mcp_client import MCPManager, resolve_env, result_text, tool_name
from escanor.core.events import ToolFinished
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.tools.base import EXTERNAL, READ, WRITE
from escanor.tools.permissions import DECLINED
from escanor.tools.registry import ToolRegistry

SERVER = str(Path(__file__).parent / "fixtures" / "mcp_notes_server.py")


def _servers(**extra):
    return {"notes": {"command": sys.executable, "args": [SERVER], **extra}}


@pytest.fixture
async def manager(tmp_path):
    registry = ToolRegistry()
    m = MCPManager(_servers(auto_approve=["add_*"]), registry, {}, log_dir=tmp_path)
    await m.start()
    yield m, registry
    await m.stop()


async def test_should_register_tools_with_permissions_from_annotations(manager):
    m, registry = manager
    tools = {name: registry.get(name) for name in ("notes__search_notes", "notes__add_note", "notes__broken")}
    assert tools["notes__search_notes"].side_effect == READ
    assert tools["notes__add_note"].side_effect == WRITE      # auto_approve pattern
    assert tools["notes__broken"].side_effect == EXTERNAL     # unannotated: ask first
    assert all(t.untrusted_output and t.group == "notes" for t in tools.values())
    assert m.group_descriptions["notes"].startswith("Team notes")
    assert registry.schemas() == []  # group is off until enabled


async def test_should_call_tools_and_report_server_errors(manager, tmp_path):
    m, registry = manager
    registry.enable("notes")
    ok = await registry.dispatch_async(ToolCall("1", "notes__search_notes", {"query": "LAUNCH"}))
    assert ok.ok and ok.output == "launch is on friday"
    failed = await registry.dispatch_async(ToolCall("2", "notes__broken", {}))
    assert failed.ok is False and "broken" in failed.output
    assert (tmp_path / "mcp-notes.log").exists()  # server stderr kept out of the chat


async def test_should_report_failed_server_without_crashing(tmp_path):
    registry = ToolRegistry()
    m = MCPManager({"ghost": {"command": "/nonexistent/server"}, "empty": {}, "off": {"command": "x", "enabled": False}},
                   registry, {}, log_dir=tmp_path)
    await m.start()
    lines = m.status_lines()
    assert any(line.startswith("ghost") and "failed" in line for line in lines)
    assert any(line.startswith("empty") and "needs either" in line for line in lines)
    assert not any(line.startswith("off") for line in lines)
    await m.stop()


async def test_should_ask_before_unannotated_tool_through_agent(tmp_path, store):
    settings = config.load_settings()
    settings["brain"]["embedder"] = "none"
    settings["skills"]["dirs"] = []
    settings["music"]["enabled"] = False
    settings["mcp"]["servers"] = _servers()
    model, spec = build_model("mock", settings)
    app = build_app(settings, model, spec)
    app.services["mcp"].servers["notes"].log_path = tmp_path / "notes.log"
    await app.start()
    try:
        app.agent.set_model(MockModel(script=[
            ModelResponse(tool_calls=[ToolCall("c0", "enable_tools", {"group": "notes"})]),
            ModelResponse(tool_calls=[ToolCall("c1", "notes__search_notes", {"query": "standup"}),
                                      ToolCall("c2", "notes__add_note", {"text": "ship it"})]),
            ModelResponse(text="Done."),
        ]), "mock:m")
        events = [e async for e in app.agent.run_turn(store.create(), "check team notes and add one")]
        results = {e.call.name: e.result for e in events if isinstance(e, ToolFinished)}
        assert results["notes__search_notes"].output.startswith('<untrusted source="notes__search_notes">')
        assert results["notes__add_note"].output == DECLINED  # no confirmer → not run
        assert "notes: Team notes" in app.agent.tools.get("enable_tools").description
    finally:
        await app.stop()


def test_tool_name_is_provider_safe():
    assert tool_name("my server", "do.thing/now") == "my_server__do_thing_now"
    assert len(tool_name("s" * 40, "t" * 40)) == 64


def test_env_values_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret")
    env = resolve_env({"TOKEN": "env:MY_TOKEN", "MODE": "x"})
    assert env["TOKEN"] == "secret" and env["MODE"] == "x" and "PATH" in env
    assert resolve_env(None) is None


def test_result_text_handles_blocks_and_structured_content():
    result = NS(content=[NS(type="text", text="hello"), NS(type="image", mime_type="image/png")], is_error=False)
    assert result_text(result) == (True, "hello\n[image: image/png]")
    assert result_text(NS(content=[], structured_content={"a": 1}, is_error=True)) == (False, '{"a": 1}')

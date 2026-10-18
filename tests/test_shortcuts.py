import subprocess
from escanor.assistant.shortcuts import ShortcutRunTool, Shortcuts, ShortcutsListTool
from escanor.core.session import SessionStore
from escanor.models.base import ToolCall
from escanor.tools.permissions import DECLINED, PermissionGate
from escanor.tools.registry import ToolRegistry


def runner(names=("Focus On", "Lights Off"), fail=False):
    calls = []

    def run(command, stdin):
        calls.append((command, stdin))
        if command[1] == "list":
            return subprocess.CompletedProcess(command, 0, "\n".join(names) + "\n", "")
        if fail:
            return subprocess.CompletedProcess(command, 1, "", "The shortcut failed.")
        return subprocess.CompletedProcess(command, 0, "done\n", "")
    return run, calls


def test_list_and_run_by_case_insensitive_name():
    run, calls = runner()
    shortcuts = Shortcuts(run)
    assert ShortcutsListTool(shortcuts).run().output == "Focus On\nLights Off"
    result = ShortcutRunTool(shortcuts).run(name="focus on", input="work")
    assert result.ok and result.output == "Ran 'Focus On'.\ndone"
    assert calls[-1] == (["shortcuts", "run", "Focus On"], "work")


def test_unknown_shortcut_is_not_run():
    run, calls = runner()
    result = ShortcutRunTool(Shortcuts(run)).run(name="Delete Everything")
    assert result.ok is False and "No shortcut named" in result.output
    assert all(c[0][1] == "list" for c in calls)


def test_failure_is_reported():
    run, _ = runner(fail=True)
    assert ShortcutRunTool(Shortcuts(run)).run(name="Lights Off").output == "The shortcut failed."


async def test_allowed_shortcuts_skip_confirmation_others_ask(tmp_path):
    run, calls = runner()
    tools = ToolRegistry([ShortcutRunTool(Shortcuts(run), allowed=["Focus On"])])
    tools.enable("assistant")
    session = SessionStore(tmp_path / "s.db").create()
    session.append_user("go")
    gate = PermissionGate()
    allowed = await gate.dispatch(ToolCall("1", "shortcut_run", {"name": "focus on"}), tools, None, session)
    asked = await gate.dispatch(ToolCall("2", "shortcut_run", {"name": "Lights Off"}), tools, None, session)
    assert allowed.ok and asked.output == DECLINED

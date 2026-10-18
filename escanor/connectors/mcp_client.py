"""
connectors/mcp_client.py

Plugs MCP servers (Model Context Protocol) into Escanor, so a Gmail,
Notion, GitHub, Slack or filesystem server becomes one more tool group
without custom code.

Configure servers in ~/.escanor/config.toml:

    [mcp.servers.github]                # local server, started by Escanor (stdio)
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-github"]
    env = { GITHUB_PERSONAL_ACCESS_TOKEN = "env:GITHUB_TOKEN" }   # "env:NAME" = read from your environment
    auto_approve = ["search_*", "get_*"]   # optional: these run without asking

    [mcp.servers.notes]                 # remote server (streamable HTTP)
    url = "https://example.com/mcp"

Each server's tools are registered as `<server>__<tool>` in a tool group
named after the server; the model switches the group on with
enable_tools when a request needs it.

Safety (tools/permissions.py):
- A tool runs without asking only when the server marks it read-only
  (readOnlyHint) or its name matches the server's `auto_approve`
  patterns. Everything else is "external": Escanor asks you first.
  Annotations come from the server, so a server you don't trust
  shouldn't be configured at all.
- Results are always untrusted_output: whatever an email or web page
  says is fenced as data.

Connections live in the running process: start() opens them one by
one (each with a timeout) and stop() closes them, from the same task --
the MCP SDK's transports require that, which is also why they aren't
opened in parallel.
"""
from __future__ import annotations
import asyncio
import fnmatch
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..tools.base import EXTERNAL, READ, WRITE, AsyncTool, ToolResult
from ..tools.registry import ToolRegistry

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 20.0
CALL_TIMEOUT = 60.0
MAX_OUTPUT = 20_000
_NAME = re.compile(r"[^a-zA-Z0-9_-]")


def tool_name(server: str, tool: str) -> str:
    """Provider-safe name (letters, digits, _ and -; at most 64 chars)."""
    name = f"{_NAME.sub('_', server)}__{_NAME.sub('_', tool)}"
    return name[:64]


def resolve_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """`"env:NAME"` values are read from Escanor's environment, so tokens
    stay in .env instead of config.toml."""
    if not env:
        return None
    resolved = dict(os.environ)
    for key, value in env.items():
        resolved[key] = os.environ.get(value[4:], "") if str(value).startswith("env:") else str(value)
    return resolved


def result_text(result: Any) -> tuple[bool, str]:
    """(ok, text) from a CallToolResult: text blocks joined, other blocks
    described, structured content as JSON when there's no text."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(block.text)
        elif kind == "image":
            parts.append(f"[image: {getattr(block, 'mime_type', 'image')}]")
        elif kind in ("resource", "embedded_resource"):
            resource = getattr(block, "resource", None)
            text = getattr(resource, "text", None)
            parts.append(text if text else f"[resource: {getattr(resource, 'uri', '?')}]")
        elif kind == "resource_link":
            parts.append(f"[link: {getattr(block, 'uri', '?')}]")
    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    text = "\n".join(parts).strip() or "(no output)"
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n[truncated at {MAX_OUTPUT} characters]"
    return not bool(getattr(result, "is_error", False)), text


class MCPTool(AsyncTool):
    untrusted_output = True

    def __init__(self, server: "ServerConnection", remote: Any, auto_approve: list[str]):
        self.server = server
        self.remote_name = remote.name
        self.name = tool_name(server.name, remote.name)
        self.group = server.name
        description = (remote.description or remote.title or remote.name).strip()
        self.description = f"[{server.name}] {description}"[:1024]
        schema = dict(remote.input_schema or {"type": "object"})
        schema.pop("$schema", None)
        schema.setdefault("type", "object")
        self.input_schema = schema
        annotations = getattr(remote, "annotations", None)
        read_only = bool(annotations and getattr(annotations, "read_only_hint", False))
        approved = any(fnmatch.fnmatch(remote.name, pattern) for pattern in auto_approve)
        self.side_effect = READ if read_only else (WRITE if approved else EXTERNAL)

    def describe_call(self, arguments: dict) -> str:
        args = json.dumps(arguments, ensure_ascii=False)
        return f"{self.server.name}: {self.remote_name} {args[:300]}"

    async def run_async(self, **arguments: Any) -> ToolResult:
        return await self.server.call(self.remote_name, arguments)


@dataclass
class ServerConnection:
    name: str
    config: dict
    log_path: Path = field(default_factory=lambda: Path.home() / ".escanor" / "logs" / "mcp.log")
    session: Any = None
    status: str = "not connected"
    tools: list[MCPTool] = field(default_factory=list)
    instructions: str = ""

    async def connect(self, stack: AsyncExitStack) -> None:
        from mcp import ClientSession, StdioServerParameters
        if self.config.get("url"):
            from mcp.client.streamable_http import streamable_http_client
            read, write = await stack.enter_async_context(streamable_http_client(self.config["url"]))
        elif self.config.get("command"):
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=self.config["command"], args=list(self.config.get("args", [])),
                                           env=resolve_env(self.config.get("env")), cwd=self.config.get("cwd"))
            # The server's own logging goes to a file, not into your chat.
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            errlog = stack.enter_context(self.log_path.open("a", encoding="utf-8"))
            read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        else:
            raise ValueError("needs either `command` (local) or `url` (remote)")
        self.session = await stack.enter_async_context(ClientSession(read, write))
        init = await self.session.initialize()
        self.instructions = (getattr(init, "instructions", None) or "").strip()
        listed = await self.session.list_tools()
        auto = list(self.config.get("auto_approve", []))
        self.tools = [MCPTool(self, remote, auto) for remote in listed.tools]
        self.status = f"connected, {len(self.tools)} tools"

    async def call(self, tool: str, arguments: dict) -> ToolResult:
        if self.session is None:
            return ToolResult(ok=False, output=f"The {self.name} connection isn't open ({self.status}).")
        try:
            result = await asyncio.wait_for(self.session.call_tool(tool, arguments), CALL_TIMEOUT)
        except asyncio.TimeoutError:
            return ToolResult(ok=False, output=f"{self.name}: {tool} took longer than {int(CALL_TIMEOUT)}s.")
        except Exception as e:  # noqa: BLE001 -- server errors are results the model can react to
            return ToolResult(ok=False, output=f"{self.name}: {tool} failed: {e}")
        ok, text = result_text(result)
        return ToolResult(ok=ok, output=text)


class MCPManager:
    def __init__(self, servers: dict[str, dict], registry: ToolRegistry, group_descriptions: dict[str, str],
                 log_dir: Path | None = None):
        self.registry = registry
        self.group_descriptions = group_descriptions
        logs = log_dir or Path.home() / ".escanor" / "logs"
        self.servers = {name: ServerConnection(name, cfg, logs / f"mcp-{name}.log")
                        for name, cfg in servers.items() if cfg.get("enabled", True)}
        self._stack: AsyncExitStack | None = None

    async def start(self) -> None:
        """Connect every server; a failing one is reported, not fatal."""
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        for server in self.servers.values():
            try:
                await asyncio.wait_for(server.connect(self._stack), CONNECT_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                server.status = f"failed: {type(e).__name__}: {e}"[:300]
                log.warning("MCP server %s: %s", server.name, server.status)
                continue
            for tool in server.tools:
                if self.registry.get(tool.name) is None:
                    self.registry.add(tool)
            names = ", ".join(t.remote_name for t in server.tools[:8])
            summary = server.instructions.splitlines()[0][:120] if server.instructions else names
            self.group_descriptions[server.name] = f"{summary} (MCP server)"

    async def stop(self) -> None:
        if self._stack is not None:
            stack, self._stack = self._stack, None
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001 -- closing a dead server must not crash shutdown
                log.exception("closing MCP servers")
            for server in self.servers.values():
                server.session = None
                server.status = "closed"

    def status_lines(self) -> list[str]:
        return [f"{s.name:<12} {s.status}" for s in self.servers.values()] or ["No MCP servers configured ([mcp.servers.*])."]

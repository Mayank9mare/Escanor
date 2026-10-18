"""A tiny MCP server for tests: one read-only tool, one that writes, one that fails."""
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("notes", instructions="Team notes: search and add notes.")
NOTES = ["standup moved to 10am", "launch is on friday"]


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def search_notes(query: str) -> str:
    """Search team notes."""
    hits = [n for n in NOTES if query.lower() in n.lower()]
    return "\n".join(hits) or "no matches"


@server.tool()
def add_note(text: str) -> str:
    """Add a team note."""
    NOTES.append(text)
    return f"added: {text}"


@server.tool()
def broken() -> str:
    """Always fails."""
    raise ValueError("database offline")


if __name__ == "__main__":
    server.run("stdio")

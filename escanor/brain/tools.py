"""
brain/tools.py

The second brain's tools.

core (always offered):  brain_search, brain_read, remember_fact
brain group (on demand): brain_write, brain_append, brain_list, forget_fact

remember_fact / forget_fact change facts.md, which goes into every
prompt, so they're affects_prompt=True: after untrusted web content was
read in a turn they need your confirmation (tools/permissions.py).
Search hits from attached read-only folders (e.g. an imported Obsidian
vault) are fenced as untrusted inside the result, since that content
may have come from anywhere.
"""
from __future__ import annotations
from ..tools.base import READ, WRITE, Tool, ToolResult
from ..tools.permissions import fence_untrusted
from .index import BrainIndex
from .vault import Vault, VaultError

BRAIN_GUIDANCE = """\
Your second brain is the user's Markdown notes (people/, projects/, processes/, ideas/, journal/,
research/). Search it (brain_search) before answering anything about the user's own life, work,
plans or past notes, and cite note paths you used. When the user tells you a durable fact about
themselves (preferences, people, routines), save it with remember_fact. To write notes, enable the
"brain" tool group."""


def is_external_copy(path: str) -> bool:
    """Saved copies of outside documents (research/<slug>/source.md) keep
    their untrusted status inside the brain."""
    return path.startswith("research/") and path.endswith("/source.md")


def _snippet(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


class BrainSearchTool(Tool):
    name = "brain_search"
    description = "Search the user's notes (second brain) by keywords and meaning. Returns note paths, headings and snippets."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "limit": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    }
    side_effect = READ

    def __init__(self, index: BrainIndex, vault: Vault | None = None):
        self._index = index
        self._vault = vault

    def run(self, query: str, limit: int = 5) -> ToolResult:
        if not query.strip():   # an empty search matches nothing; it once read as "the brain is empty" (B26)
            notes = [n for n in self._vault.list() if n != "facts.md"] if self._vault else []
            listing = "\n".join(f"- {n} — {self._vault.title(n)}" for n in notes) or "(no notes yet)"
            return ToolResult(ok=True, output=f"Give words to search for. All notes:\n{listing}")
        hits = self._index.search(query, limit=max(1, min(int(limit or 5), 20)))
        if not hits:
            return ToolResult(ok=True, output=f"No notes match '{query}'.")
        blocks = []
        for hit in hits:
            where = hit.path if hit.source == "brain" else f"{hit.source}:{hit.path}"
            block = f"[{where} § {hit.heading}]\n{_snippet(hit.text)}"
            if hit.source != "brain":
                block = fence_untrusted(f"notes:{hit.source}", block)
            elif is_external_copy(hit.path):
                block = fence_untrusted(hit.path, block)
            blocks.append(block)
        return ToolResult(ok=True, output="\n\n".join(blocks))


class BrainReadTool(Tool):
    name = "brain_read"
    description = "Read a whole note from the second brain, e.g. 'projects/escanor.md'."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Note path inside the brain."}},
        "required": ["path"],
    }
    side_effect = READ

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, path: str) -> ToolResult:
        try:
            text = self._vault.read(path)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        relative = self._vault.relative(self._vault.resolve(path))
        return ToolResult(ok=True, output=fence_untrusted(relative, text) if is_external_copy(relative) else text)


class RememberFactTool(Tool):
    name = "remember_fact"
    description = (
        "Save one durable fact about the user to facts.md (kept in mind in every conversation), e.g. "
        "'Prefers window seats on flights' or 'Salary is 3 lakhs a month'. Use it whenever the user tells you "
        "something lasting about themselves: job, family, money, health, goals, plans, preferences. Only facts "
        "the user stated about themselves; one fact per call."
    )
    input_schema = {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "One short sentence."}},
        "required": ["fact"],
    }
    side_effect = WRITE
    affects_prompt = True

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, fact: str) -> ToolResult:
        try:
            added = self._vault.add_fact(fact)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output="Saved." if added else "Already known.")


class ForgetFactTool(Tool):
    name = "forget_fact"
    description = "Remove facts from facts.md that contain the given words."
    input_schema = {
        "type": "object",
        "properties": {"match": {"type": "string", "description": "Words the fact contains."}},
        "required": ["match"],
    }
    side_effect = WRITE
    group = "brain"
    affects_prompt = True

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, match: str) -> ToolResult:
        try:
            removed = self._vault.forget_facts(match)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        if not removed:
            return ToolResult(ok=True, output=f"No fact contains '{match}'.")
        return ToolResult(ok=True, output="Forgot:\n" + "\n".join(f"- {f}" for f in removed))


class BrainWriteTool(Tool):
    name = "brain_write"
    description = (
        "Create a note in the second brain (Markdown). Pick the folder: people/, projects/, processes/, "
        "ideas/, journal/ or research/. Set overwrite=true only to replace a note on purpose; the old "
        "version is kept in history."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "e.g. 'ideas/home-automation.md'"},
            "content": {"type": "string", "description": "The full Markdown text."},
            "overwrite": {"type": "boolean"},
        },
        "required": ["path", "content"],
    }
    side_effect = WRITE
    group = "brain"

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, path: str, content: str, overwrite: bool = False) -> ToolResult:
        try:
            written, replaced = self._vault.write(path, content, overwrite=bool(overwrite))
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"{'Replaced' if replaced else 'Created'} {written}.")


class BrainAppendTool(Tool):
    name = "brain_append"
    description = (
        "Add text to the end of a note (created if missing). Use path 'journal' for a timestamped entry "
        "in today's journal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "text": {"type": "string"},
            "heading": {"type": "string", "description": "Optional section heading for the added text."},
        },
        "required": ["path", "text"],
    }
    side_effect = WRITE
    group = "brain"

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, path: str, text: str, heading: str | None = None) -> ToolResult:
        try:
            written = self._vault.journal(text) if path.strip().strip("/") in ("journal", "journal.md") \
                else self._vault.append(path, text, heading)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output=f"Added to {written}.")


class BrainListTool(Tool):
    name = "brain_list"
    description = "List notes in the second brain, optionally within one folder."
    input_schema = {"type": "object", "properties": {"folder": {"type": "string"}}}
    side_effect = READ
    group = "brain"

    def __init__(self, vault: Vault):
        self._vault = vault

    def run(self, folder: str = "") -> ToolResult:
        try:
            notes = self._vault.list(folder)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        return ToolResult(ok=True, output="\n".join(notes) if notes else "(no notes yet)")


def brain_tools(vault: Vault, index: BrainIndex) -> list[Tool]:
    return [
        BrainSearchTool(index, vault), BrainReadTool(vault), RememberFactTool(vault),
        BrainWriteTool(vault), BrainAppendTool(vault), BrainListTool(vault), ForgetFactTool(vault),
    ]

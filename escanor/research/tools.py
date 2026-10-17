"""
research/tools.py

The "research" tool group:

- research_ingest: save a paper/article (arXiv id, link, local PDF) into
  the brain and return its opening for the model to start explaining.
- research_read: page through a saved source (papers are long).
- research_reviews: learning-log topics due for spaced review.

Explaining itself is the learn-anything skill's job (use_skill), which
writes its learning log to research/learn/<topic>.md via brain_write.
"""
from __future__ import annotations
from datetime import date
from typing import Callable
from ..brain.vault import Vault, VaultError
from ..tools.base import READ, WRITE, Tool, ToolResult
from ..assistant.web import BlockedURL
from .ingest import Ingestor
from .reviews import describe, due_reviews

GROUP = "research"
GROUP_DESCRIPTION = "save and read papers/articles (arXiv, links, PDFs); learning reviews due"
PAGE_CHARS = 12_000

RESEARCH_GUIDANCE = """\
To explain a paper or article: enable the "research" tool group, research_ingest it, read it in parts
with research_read, and use the learn-anything skill to teach it (check understanding, build a model
card, save the learning log to research/learn/<topic>.md). Explain in plain language first, then detail."""


class IngestTool(Tool):
    name = "research_ingest"
    description = (
        "Save a paper or article into the second brain: an arXiv id or link, any web link (HTML or PDF), "
        "or a PDF in Downloads/Documents/Desktop. Returns where it was saved and its opening."
    )
    input_schema = {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]}
    side_effect = WRITE
    group = GROUP
    untrusted_output = True

    def __init__(self, ingestor: Ingestor):
        self.ingestor = ingestor

    def run(self, source: str) -> ToolResult:
        try:
            path, document = self.ingestor.ingest(source)
        except (BlockedURL, RuntimeError, ValueError, PermissionError, FileNotFoundError, OSError) as e:
            return ToolResult(ok=False, output=str(e))
        pages = max(1, -(-len(document.text) // PAGE_CHARS))
        opening = document.text[:3000]
        return ToolResult(ok=True, output=(
            f"Saved '{document.title}' to {path} ({len(document.text):,} characters, {pages} part(s) "
            f"via research_read).\n\n{opening}"
        ))


class ReadTool(Tool):
    name = "research_read"
    description = "Read a saved research source in parts: part 1, 2, ... of about 12,000 characters."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "e.g. research/attention-is-all-you-need/source.md"},
                       "part": {"type": "integer"}},
        "required": ["path"],
    }
    side_effect = READ
    group = GROUP
    untrusted_output = True

    def __init__(self, vault: Vault):
        self.vault = vault

    def run(self, path: str, part: int = 1) -> ToolResult:
        try:
            text = self.vault.read(path)
        except VaultError as e:
            return ToolResult(ok=False, output=str(e))
        pages = max(1, -(-len(text) // PAGE_CHARS))
        part = max(1, min(int(part or 1), pages))
        chunk = text[(part - 1) * PAGE_CHARS: part * PAGE_CHARS]
        more = f"\n\n[part {part} of {pages}" + (f"; next: part={part + 1}]" if part < pages else "; end]")
        return ToolResult(ok=True, output=chunk + more)


class ReviewsTool(Tool):
    name = "research_reviews"
    description = "List learning topics due for spaced review (from learn-anything logs)."
    input_schema = {"type": "object", "properties": {}}
    side_effect = READ
    group = GROUP

    def __init__(self, vault: Vault, today: Callable[[], date] = date.today):
        self.vault = vault
        self._today = today

    def run(self) -> ToolResult:
        today = self._today()
        return ToolResult(ok=True, output=describe(due_reviews(self.vault, today), today) or "Nothing due for review.")


def research_tools(ingestor: Ingestor, vault: Vault) -> list[Tool]:
    return [IngestTool(ingestor), ReadTool(vault), ReviewsTool(vault)]

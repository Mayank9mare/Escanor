"""
brain/vault.py

The second brain's storage: plain Markdown files under
~/.escanor/brain/, readable and editable in any editor (or Obsidian).

  people/ projects/ processes/ ideas/ journal/ research/   notes
  facts.md                                                 durable facts about you,
                                                           put in every prompt

Every path the model gives is confined to the vault (no "../", no
absolute paths) and must be Markdown. Overwriting a note first copies
the old version to ~/.escanor/brain-history/, so nothing the model does
here loses your writing.

Extra folders (e.g. an existing Obsidian vault) can be attached
read-only: they're searchable, never written.
"""
from __future__ import annotations
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Callable

FOLDERS = ("people", "projects", "processes", "ideas", "journal", "research")
FACTS_FILE = "facts.md"
FACTS_HEADER = "# Facts about me\n\nDurable facts Escanor keeps in mind. Edit freely.\n\n"
FACT_LINE = re.compile(r"^- (.+?)(?:  _\(added (\d{4}-\d{2}-\d{2})\)_)?$")


class VaultError(ValueError):
    pass


class Vault:
    def __init__(self, root: Path, history: Path | None = None, extra_dirs: list[Path] | None = None,
                 today: Callable[[], date] = date.today, now: Callable[[], datetime] = datetime.now):
        self.root = root.expanduser()
        self.history = (history or self.root.parent / "brain-history").expanduser()
        self.extra_dirs = [d.expanduser() for d in extra_dirs or [] if d.expanduser().is_dir()]
        self._today = today
        self._now = now
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in FOLDERS:
            (self.root / folder).mkdir(exist_ok=True)
        if not (self.root / FACTS_FILE).exists():
            (self.root / FACTS_FILE).write_text(FACTS_HEADER, encoding="utf-8")

    # ---------- paths ----------

    def resolve(self, relative: str) -> Path:
        """A vault path for `relative` ("projects/escanor" -> projects/escanor.md)."""
        relative = relative.strip().replace("\\", "/")
        if not relative or relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
            raise VaultError("Give a path inside the brain, like 'projects/escanor.md'.")
        if not relative.endswith(".md"):
            relative += ".md"
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()):
            raise VaultError(f"'{relative}' is outside the brain.")
        return target

    def relative(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root.resolve()))

    # ---------- notes ----------

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise VaultError(f"No note '{self.relative(path)}'.")
        return path.read_text(encoding="utf-8")

    def exists(self, relative: str) -> bool:
        return self.resolve(relative).is_file()

    def write(self, relative: str, content: str, overwrite: bool = False) -> tuple[str, bool]:
        """Create (or, with overwrite=True, replace) a note. Returns
        (relative path, replaced). The old version is kept in history."""
        path = self.resolve(relative)
        replaced = path.exists()
        if replaced and not overwrite:
            raise VaultError(f"'{self.relative(path)}' already exists. Use brain_append, or overwrite=true to replace it.")
        if replaced:
            self._backup(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return self.relative(path), replaced

    def append(self, relative: str, text: str, heading: str | None = None) -> str:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        block = (f"\n## {heading}\n\n" if heading else "\n") + text.strip() + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        return self.relative(path)

    def journal(self, text: str) -> str:
        """Append a timestamped entry to today's journal note."""
        return self.append(f"journal/{self._today().isoformat()}", text, heading=self._now().strftime("%H:%M"))

    def list(self, folder: str = "") -> list[str]:
        base = (self.root / folder).resolve() if folder else self.root.resolve()
        if not base.is_relative_to(self.root.resolve()) or not base.is_dir():
            raise VaultError(f"No folder '{folder}' in the brain.")
        return sorted(self.relative(p) for p in base.rglob("*.md"))

    def _backup(self, path: Path) -> None:
        stamp = self._now().strftime("%Y%m%d-%H%M%S")
        target = self.history / f"{self.relative(path)[:-3]}.{stamp}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)

    # ---------- facts ----------

    def facts(self) -> list[str]:
        lines = (self.root / FACTS_FILE).read_text(encoding="utf-8").splitlines()
        return [m.group(1) for line in lines if (m := FACT_LINE.match(line.strip()))]

    def add_fact(self, fact: str) -> bool:
        """Add a one-line fact. Returns False if it's already there."""
        fact = " ".join(fact.split()).strip("- ").strip()
        if not fact:
            raise VaultError("Empty fact.")
        if fact.lower() in (f.lower() for f in self.facts()):
            return False
        with (self.root / FACTS_FILE).open("a", encoding="utf-8") as f:
            f.write(f"- {fact}  _(added {self._today().isoformat()})_\n")
        return True

    def forget_facts(self, query: str) -> list[str]:
        """Remove facts containing `query` (case-insensitive). Returns them."""
        query = query.strip().lower()
        if not query:
            raise VaultError("Say which fact to forget.")
        path = self.root / FACTS_FILE
        kept, removed = [], []
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
            match = FACT_LINE.match(line.strip())
            if match and query in match.group(1).lower():
                removed.append(match.group(1))
            else:
                kept.append(line)
        if removed:
            self._backup(path)
            path.write_text("".join(kept), encoding="utf-8")
        return removed

    def facts_section(self, limit_chars: int = 6000) -> str:
        """The facts for the system prompt, newest kept if over the limit."""
        facts = self.facts()
        if not facts:
            return ""
        lines: list[str] = []
        used = 0
        for fact in reversed(facts):
            used += len(fact) + 3
            if used > limit_chars:
                break
            lines.append(f"- {fact}")
        lines.reverse()
        return "What you know about the user (from their facts.md; prefer it over guesses):\n" + "\n".join(lines)

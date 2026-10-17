"""
skills/loader.py

Finds skills -- folders with a SKILL.md, the format used by Claude Code
skills such as ~/projects/mayank-awesome-skills -- and loads them on
demand.

Only each skill's name and one-line description go into the system
prompt (skill_index_section). The full SKILL.md body is fetched only
when the model calls use_skill(name), and the files it references
(references/*.md, templates/*.md) only via read_skill_file. That keeps
every request small: the learn-anything skill alone is several thousand
tokens.

Skills are the user's own files, so their content is trusted (not fenced
as untrusted), but only allow-listed skills are offered: most of that
repo is engineering and work tooling that makes no sense for a personal
assistant.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

_MAX_FILE_CHARS = 40_000


@dataclass
class Skill:
    name: str
    description: str
    directory: Path

    def body(self) -> str:
        """SKILL.md without its frontmatter."""
        return split_frontmatter((self.directory / "SKILL.md").read_text(encoding="utf-8"))[1].strip()

    def files(self) -> list[str]:
        """Other text files in the skill, relative to its folder."""
        return sorted(
            str(p.relative_to(self.directory)) for p in self.directory.rglob("*")
            if p.is_file() and p.name != "SKILL.md" and p.suffix in (".md", ".txt") and not p.name.startswith(".")
        )

    def read(self, relative: str) -> str:
        """A file inside the skill folder. Refuses paths that escape it."""
        target = (self.directory / relative).resolve()
        if not target.is_relative_to(self.directory.resolve()):
            raise ValueError(f"'{relative}' is outside the {self.name} skill.")
        if not target.is_file():
            raise FileNotFoundError(f"No file '{relative}' in the {self.name} skill. Files: {', '.join(self.files()) or 'none'}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_FILE_CHARS:
            text = text[:_MAX_FILE_CHARS] + f"\n\n[truncated at {_MAX_FILE_CHARS} characters]"
        return text


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the `---`-fenced header of a SKILL.md: `key: value` lines and
    YAML block scalars (`key: >-` / `key: |` followed by indented lines).
    Enough for skill files; not a general YAML parser."""
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---\s*$", text[3:], re.MULTILINE)
    if not end:
        return {}, text
    header = text[3:3 + end.start()]
    body = text[3 + end.end():]
    fields: dict[str, str] = {}
    lines = header.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", lines[i])
        i += 1
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in (">", ">-", "|", "|-", ">+", "|+"):
            block: list[str] = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            value = (" " if value.startswith(">") else "\n").join(b for b in block if b)
        fields[key] = value.strip().strip('"').strip("'")
    return fields, body


def discover(directories: list[Path], allow: list[str] | None = None) -> dict[str, Skill]:
    """Every skill under `directories` (first one wins on a name clash),
    filtered to `allow` if given. Missing directories are skipped."""
    skills: dict[str, Skill] = {}
    for directory in directories:
        directory = directory.expanduser()
        if not directory.is_dir():
            continue
        for skill_file in sorted(directory.glob("*/SKILL.md")):
            try:
                fields, _ = split_frontmatter(skill_file.read_text(encoding="utf-8"))
            except OSError:
                continue
            name = fields.get("name") or skill_file.parent.name
            if name in skills or (allow is not None and name not in allow):
                continue
            skills[name] = Skill(name=name, description=fields.get("description", ""), directory=skill_file.parent)
    return skills


SKILL_RUNTIME_NOTE = """\
Skills were written for a coding assistant, so map their instructions onto your own tools:
- the skill's own files (references/, templates/) -> read_skill_file
- reading the user's notes -> brain_search / brain_read
- saving files -> brain_write / brain_append (enable the "brain" tool group first). A path like
  ~/.learn/<topic>.md becomes research/learn/<topic>.md in the brain.
If a skill asks for something no tool can do, say so and give the user the text instead.
Ask questions one at a time, and keep spoken answers short."""


def skill_index_section(skills: dict[str, Skill]) -> str:
    if not skills:
        return ""
    lines = [f"- {s.name}: {s.description}" for s in skills.values()]
    return (
        "Skills: playbooks you can load with use_skill when a request matches one. Load at most one "
        "per request unless asked.\n" + "\n".join(lines)
    )

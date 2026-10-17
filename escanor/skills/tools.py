"""
skills/tools.py

use_skill and read_skill_file: how the model pulls a skill's
instructions into the conversation when it needs them (see loader.py).
"""
from __future__ import annotations
from ..tools.base import READ, Tool, ToolResult
from .loader import SKILL_RUNTIME_NOTE, Skill


class UseSkillTool(Tool):
    name = "use_skill"
    description = "Load a skill's full instructions (see the Skills list in your instructions), then follow them."
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "The skill's name."}},
        "required": ["name"],
    }
    side_effect = READ

    def __init__(self, skills: dict[str, Skill]):
        self._skills = skills

    def run(self, name: str) -> ToolResult:
        skill = self._skills.get(name)
        if skill is None:
            return ToolResult(ok=False, output=f"No skill '{name}'. Available: {', '.join(self._skills) or 'none'}.")
        files = skill.files()
        listing = ("\n\nFiles in this skill (load with read_skill_file):\n" + "\n".join(f"- {f}" for f in files)) if files else ""
        return ToolResult(ok=True, output=f"# Skill: {skill.name}\n\n{SKILL_RUNTIME_NOTE}\n\n---\n\n{skill.body()}{listing}")


class ReadSkillFileTool(Tool):
    name = "read_skill_file"
    description = "Read one of a loaded skill's own files, e.g. references/socratic.md."
    input_schema = {
        "type": "object",
        "properties": {
            "skill": {"type": "string", "description": "The skill's name."},
            "path": {"type": "string", "description": "Path inside the skill folder, as listed by use_skill."},
        },
        "required": ["skill", "path"],
    }
    side_effect = READ

    def __init__(self, skills: dict[str, Skill]):
        self._skills = skills

    def run(self, skill: str, path: str) -> ToolResult:
        found = self._skills.get(skill)
        if found is None:
            return ToolResult(ok=False, output=f"No skill '{skill}'.")
        try:
            return ToolResult(ok=True, output=found.read(path))
        except (ValueError, FileNotFoundError) as e:
            return ToolResult(ok=False, output=str(e))

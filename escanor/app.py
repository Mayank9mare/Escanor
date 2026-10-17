"""
app.py

Builds a ready-to-use Agent from settings: the model, the tool registry,
the permission gate and the system prompt sections. Every interface
(CLI now; web UI and voice later) calls build_agent() so they all get
the same Escanor.
"""
from __future__ import annotations
from pathlib import Path
from . import config
from .assistant.web import WebFetchTool, WebSearchTool
from .core.agent import Agent
from .core.prompts import PromptBuilder
from .models.base import Model
from .models.registry import get_model
from .skills.loader import Skill, discover, skill_index_section
from .skills.tools import ReadSkillFileTool, UseSkillTool
from .tools.permissions import UNTRUSTED_GUIDANCE, PermissionGate
from .tools.registry import ToolRegistry
from .tools.system_tools import EnableToolsTool, GetTimeTool, SwitchModelTool


def build_model(name: str, settings: dict) -> tuple[Model, str]:
    """Resolve an alias/spec and construct the model. Raises ValueError for
    an unknown name, RuntimeError for a missing key or package."""
    provider, model_id = config.resolve_model(name, settings)
    model = get_model(provider, model_id, role="chat", effort=settings["models"].get("effort"))
    return model, f"{provider}:{model_id}"


def build_agent(settings: dict, model: Model, model_spec: str) -> tuple[Agent, dict[str, Skill]]:
    """The agent, plus the skills it was given (for listing in interfaces)."""
    skills = discover([Path(d) for d in settings["skills"]["dirs"]], allow=settings["skills"].get("allow"))

    registry = ToolRegistry()
    prompts = PromptBuilder(sections=[lambda: UNTRUSTED_GUIDANCE, lambda: skill_index_section(skills)])
    agent = Agent(model, model_spec, tools=registry, prompts=prompts, gate=PermissionGate(),
                  max_steps=settings["agent"]["max_steps"])

    def request_switch(name: str) -> str:
        new_model, spec = build_model(name, settings)
        agent.request_model_switch(new_model, spec)
        return spec

    for tool in (
        GetTimeTool(),
        WebSearchTool(),
        WebFetchTool(allow_private=settings["web"]["allow_private_hosts"]),
        SwitchModelTool(request_switch, settings["models"]["aliases"]),
        EnableToolsTool(registry),
    ):
        registry.add(tool)
    if skills:
        registry.add(UseSkillTool(skills))
        registry.add(ReadSkillFileTool(skills))
    return agent, skills

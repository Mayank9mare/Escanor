"""
app.py

Builds a ready-to-use Escanor from settings: the model, the agent, its
tools, the permission gate, the system prompt sections, and the services
behind them (the brain today; voice and music in later phases). Every
interface (CLI, web UI, voice loop) calls build_app() so they all get
the same Escanor.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from . import config
from .assistant.web import WebFetchTool, WebSearchTool
from .brain.embeddings import Embedder, HashEmbedder, NoEmbedder, OllamaEmbedder
from .brain.index import BrainIndex
from .brain.tools import BRAIN_GUIDANCE, brain_tools
from .brain.vault import Vault
from .core.agent import Agent
from .core.prompts import PromptBuilder
from .models.base import Model
from .models.registry import get_model
from .skills.loader import Skill, discover, skill_index_section
from .skills.tools import ReadSkillFileTool, UseSkillTool
from .tools.permissions import UNTRUSTED_GUIDANCE, PermissionGate
from .tools.registry import ToolRegistry
from .tools.system_tools import EnableToolsTool, GetTimeTool, SwitchModelTool

GROUP_DESCRIPTIONS = {
    "brain": "write, append to and list notes in the second brain; forget facts",
    "music": "play, queue and control YouTube Music; DJ mode with spoken intros",
}


@dataclass
class Escanor:
    agent: Agent
    settings: dict
    skills: dict[str, Skill]
    vault: Vault
    index: BrainIndex
    services: dict[str, object] = field(default_factory=dict)


def build_model(name: str, settings: dict) -> tuple[Model, str]:
    """Resolve an alias/spec and construct the model. Raises ValueError for
    an unknown name, RuntimeError for a missing key or package."""
    provider, model_id = config.resolve_model(name, settings)
    model = get_model(provider, model_id, role="chat", effort=settings["models"].get("effort"))
    return model, f"{provider}:{model_id}"


def build_embedder(settings: dict) -> Embedder:
    kind = settings["brain"]["embedder"]
    if kind == "ollama":
        return OllamaEmbedder(model=settings["brain"]["embed_model"])
    if kind == "hash":
        return HashEmbedder()
    return NoEmbedder()


def build_brain(settings: dict) -> tuple[Vault, BrainIndex]:
    home = config.data_dir()
    extra = {Path(d).expanduser().name or f"extra{i}": Path(d) for i, d in enumerate(settings["brain"]["extra_dirs"])}
    vault = Vault(home / "brain", history=home / "brain-history", extra_dirs=list(extra.values()))
    index = BrainIndex(home / "brain-index.db", {"brain": vault.root, **extra}, build_embedder(settings))
    return vault, index


def build_app(settings: dict, model: Model, model_spec: str) -> Escanor:
    skills = discover([Path(d) for d in settings["skills"]["dirs"]], allow=settings["skills"].get("allow"))
    vault, index = build_brain(settings)

    registry = ToolRegistry()
    prompts = PromptBuilder(sections=[
        lambda: UNTRUSTED_GUIDANCE,
        lambda: BRAIN_GUIDANCE,
        vault.facts_section,
        lambda: skill_index_section(skills),
    ])
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
        EnableToolsTool(registry, GROUP_DESCRIPTIONS),
        *brain_tools(vault, index),
    ):
        registry.add(tool)
    if skills:
        registry.add(UseSkillTool(skills))
        registry.add(ReadSkillFileTool(skills))
    escanor = Escanor(agent=agent, settings=settings, skills=skills, vault=vault, index=index)
    if settings["music"]["enabled"]:
        install_music(escanor)
    return escanor


def install_music(escanor: Escanor) -> None:
    """YouTube Music: player + library + DJ, their tools, and the ducker that
    turns the music down while Escanor talks. The DJ's voice (`dj.say`) is
    attached by voice/runtime.py when there's a speaker."""
    from .music.dj import DJ, SpendGuard
    from .music.library import MusicLibrary
    from .music.player import PlayerDucker, YouTubeMusicPlayer
    from .music.tools import music_tools

    music = escanor.settings["music"]
    home = config.data_dir()
    player = YouTubeMusicPlayer(music["api_url"], token_path=home / "ytm_token")
    library = MusicLibrary(auth_path=home / "ytmusic_auth.json")
    try:
        dj_model, _ = build_model(music["dj_model"], escanor.settings)
    except (ValueError, RuntimeError):
        dj_model = None  # no key: the DJ still picks (first radio track) but stays quiet
    dj = DJ(player, library, dj_model, SpendGuard(home / "dj_usage.json", int(music["daily_model_calls"])),
            patter_every=int(music["patter_every"]))
    for tool in music_tools(player, library, dj):
        escanor.agent.tools.add(tool)
    escanor.services.update(player=player, library=library, dj=dj,
                            ducker=PlayerDucker(YouTubeMusicPlayer(music["api_url"], home / "ytm_token", timeout=1.5),
                                                level=int(music["duck_level"])))

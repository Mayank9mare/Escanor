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
from .core.costs import DailyBudget
from .core import persona
from .core.persona import persona_section
from .core.router import Router
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
    "assistant": "Apple Reminders and Calendar, opening apps/links, notifications, clipboard, timers",
    "research": "save and read papers/articles (arXiv, links, PDFs); learning reviews due",
}


@dataclass
class Escanor:
    agent: Agent
    settings: dict
    skills: dict[str, Skill]
    vault: Vault
    index: BrainIndex
    services: dict[str, object] = field(default_factory=dict)
    # Async setup/teardown for things that live in the event loop (MCP
    # connections, proactive mode). Every interface awaits start() once
    # its loop is running and stop() before it exits, from the same task.
    on_start: list = field(default_factory=list)
    on_stop: list = field(default_factory=list)

    async def start(self) -> None:
        for hook in self.on_start:
            await hook()

    async def stop(self) -> None:
        for hook in reversed(self.on_stop):
            try:
                await hook()
            except Exception:  # noqa: BLE001 -- one failing shutdown mustn't skip the rest
                pass


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
    persona.configure(settings["persona"]["style"])
    skills = discover([Path(d) for d in settings["skills"]["dirs"]], allow=settings["skills"].get("allow"))
    vault, index = build_brain(settings)

    registry = ToolRegistry()
    prompts = PromptBuilder(sections=[
        lambda: persona_section(settings["persona"]["style"]),
        lambda: UNTRUSTED_GUIDANCE,
        lambda: BRAIN_GUIDANCE,
        vault.facts_section,
        lambda: skill_index_section(skills),
    ])
    agent = Agent(model, model_spec, tools=registry, prompts=prompts, gate=PermissionGate(),
                  max_steps=settings["agent"]["max_steps"])
    if settings["router"].get("escalate_to"):
        agent.router = Router(lambda name: build_model(name, settings), settings["router"]["escalate_to"],
                              int(settings["router"].get("after_steps", 0)))
    if settings["models"].get("fallbacks"):
        from .core.failover import Failover
        agent.failover = Failover(lambda name: build_model(name, settings), settings["models"]["fallbacks"],
                                  cooldown_seconds=60 * float(settings["models"].get("fallback_cooldown_minutes", 15)))
    if float(settings["budget"].get("daily_usd", 0)) > 0:
        from . import telemetry
        agent.budget = DailyBudget(float(settings["budget"]["daily_usd"]),
                                   lambda since: telemetry.get_store().usage_since(since),
                                   settings["budget"].get("prices", {}))

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
    if settings["assistant"]["enabled"]:
        install_assistant(escanor)
    if settings["research"]["enabled"]:
        install_research(escanor)
    if settings["mcp"]["servers"]:
        install_mcp(escanor)
    return escanor


def install_mcp(escanor: Escanor) -> None:
    """MCP servers from [mcp.servers.*]; connected when the interface starts."""
    from .connectors.mcp_client import MCPManager
    manager = MCPManager(escanor.settings["mcp"]["servers"], escanor.agent.tools, GROUP_DESCRIPTIONS,
                         log_dir=config.data_dir() / "logs")
    escanor.services["mcp"] = manager
    escanor.on_start.append(manager.start)
    escanor.on_stop.append(manager.stop)


def install_research(escanor: Escanor) -> None:
    """Paper/article ingestion, paged reading, and spaced-review reminders
    (also added to the morning briefing when the assistant is installed)."""
    from datetime import date
    from .research.ingest import Ingestor
    from .research.reviews import describe, due_reviews
    from .research.tools import RESEARCH_GUIDANCE, research_tools

    settings = escanor.settings
    ingestor = Ingestor(escanor.vault, [Path(d) for d in settings["research"]["allowed_dirs"]],
                        allow_private=settings["web"]["allow_private_hosts"])
    for tool in research_tools(ingestor, escanor.vault):
        escanor.agent.tools.add(tool)
    escanor.agent.prompts.add_section(lambda: RESEARCH_GUIDANCE)
    briefing = escanor.services.get("briefing")
    if briefing is not None:
        briefing.add("Learning reviews due", lambda: describe(due_reviews(escanor.vault, date.today()), date.today()))
    escanor.services["ingestor"] = ingestor


def install_assistant(escanor: Escanor) -> None:
    """Reminders, Calendar, Mac actions, timers and the morning briefing.
    Timers announce through every channel attached to `timers.announcers`:
    a notification here, speech (voice runtime) and printing (CLI)."""
    from .assistant.apple import Apple, apple_tools, format_events
    from .assistant.briefing import Briefing, BriefingTool, due_today_first
    from .assistant.timers import SetTimerTool, TimerCancelTool, TimersListTool, TimerService

    apple = Apple()
    timers = TimerService()
    timers.announcers.append(lambda message: apple.notify("Escanor", message))

    briefing = Briefing()
    midnight = lambda: apple.now().replace(hour=0, minute=0, second=0, microsecond=0)  # noqa: E731
    briefing.add("Calendar today", lambda: format_events(apple.events(midnight(), 1)))
    briefing.add("Reminders", lambda: due_today_first(apple.reminders(), apple.now().date().isoformat()))
    briefing.add("Timers", lambda: "\n".join(timers.describe(t) for t in timers.timers.values()))
    from .assistant.news import News, NewsTool
    news = News(escanor.settings["assistant"].get("news_region", "IN:en"))
    escanor.agent.tools.add(NewsTool(news))
    briefing.add("Top headlines", lambda: news.text(limit=5))
    from .assistant.shortcuts import ShortcutRunTool, Shortcuts, ShortcutsListTool
    shortcuts = Shortcuts()
    escanor.agent.tools.add(ShortcutsListTool(shortcuts))
    escanor.agent.tools.add(ShortcutRunTool(shortcuts, escanor.settings["assistant"].get("shortcuts_allowed", [])))
    from .assistant.weather import Weather, WeatherTool
    city = escanor.settings["assistant"].get("city", "")
    weather = Weather(city)
    escanor.agent.tools.add(WeatherTool(weather))
    if city:
        briefing.add(f"Weather in {city}", lambda: weather.report())
    briefing.add("Latest journal entry", lambda: _latest_journal(escanor.vault))

    for tool in (*apple_tools(apple), SetTimerTool(timers), TimersListTool(timers), TimerCancelTool(timers),
                 BriefingTool(briefing)):
        escanor.agent.tools.add(tool)
    if escanor.settings["assistant"].get("screen", True):
        from .assistant.screen import LookAtScreenTool, Screen
        screen = Screen(config.data_dir() / "screens")
        escanor.agent.tools.add(LookAtScreenTool(screen, lambda: escanor.agent.model_spec))
    escanor.services.update(apple=apple, timers=timers, briefing=briefing)
    if escanor.settings["proactive"]["enabled"]:
        install_proactive(escanor, apple, timers)


def install_proactive(escanor: Escanor, apple, timers) -> None:
    """Meeting heads-ups, evening wrap-up, break nudges. Shares the timers'
    announcers, so it speaks/notifies/prints wherever timers do."""
    from datetime import date
    from .assistant.proactive import Proactive, ProactivePauseTool
    from .research.reviews import due_reviews

    def related_note(title: str) -> str | None:
        hits = escanor.index.search(title, limit=1)
        return hits[0].path if hits and hits[0].source == "brain" else None

    proactive = Proactive(
        escanor.settings["proactive"], timers.announcers, events=apple.events, reminders=apple.reminders,
        related_note=related_note, reviews_due=lambda: len(due_reviews(escanor.vault, date.today())),
    )
    escanor.agent.tools.add(ProactivePauseTool(proactive))
    escanor.services["proactive"] = proactive
    escanor.on_start.append(proactive.start)
    escanor.on_stop.append(proactive.stop)


def _latest_journal(vault: Vault) -> str:
    entries = vault.list("journal")
    if not entries:
        return ""
    text = vault.read(entries[-1])
    return f"{entries[-1]}:\n{text[-600:].strip()}"


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
            patter_every=int(music["patter_every"]), style=music.get("dj_style", "talk_over"))
    for tool in music_tools(player, library, dj):
        escanor.agent.tools.add(tool)
    escanor.services.update(player=player, library=library, dj=dj,
                            ducker=PlayerDucker(YouTubeMusicPlayer(music["api_url"], home / "ytm_token", timeout=1.5),
                                                level=int(music["duck_level"])))

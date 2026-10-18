from datetime import datetime
import pytest
from escanor import config
from escanor.app import build_app, build_model
from escanor.core.persona import dj_voice, line, persona_section, phase


@pytest.mark.parametrize("hour,minute,expected", [
    (5, 59, "night"), (6, 0, "dawn"), (8, 59, "dawn"), (9, 0, "day"), (11, 29, "day"), (11, 30, "noon"),
    (12, 29, "noon"), (12, 30, "afternoon"), (17, 59, "afternoon"), (18, 0, "night"), (23, 0, "night"),
])
def test_phase_follows_the_sun(hour, minute, expected):
    assert phase(datetime(2026, 10, 18, hour, minute)) == expected


def test_sections_by_phase_and_plain_switches_off():
    assert "The One" in persona_section("escanor", datetime(2026, 10, 18, 12, 0))
    assert "proud" in persona_section("escanor", datetime(2026, 10, 18, 9, 0))
    assert "self-effacing" in persona_section("escanor", datetime(2026, 10, 18, 21, 0))
    assert persona_section("plain", datetime(2026, 10, 18, 12, 0)) == ""


def test_persona_comes_after_working_rules_in_prompt():
    settings = config.load_settings()
    settings["brain"]["embedder"] = "none"
    settings["skills"]["dirs"] = []
    model, spec = build_model("mock", settings)
    prompt = build_app(settings, model, spec).agent.prompts.system_prompt()
    assert prompt.index("Never invent facts") < prompt.index("Character: you are Escanor")


def test_core_character_holds_all_day():
    for hour in (7, 10, 12, 15, 22):
        section = persona_section("escanor", datetime(2026, 10, 18, hour, 0))
        assert "Lion's Sin of Pride" in section and "never bluff" in section and "Who decided that?" in section


def test_fixed_lines_follow_the_sun_and_plain_is_neutral():
    noon, night = datetime(2026, 10, 18, 12, 0), datetime(2026, 10, 18, 22, 0)
    assert line("confirm", "escanor", noon, summary="run your shortcut 'Focus'") == \
        "Shall I run your shortcut 'Focus'? One word from you, and it is done."
    assert "sorry" in line("error", "escanor", night).lower()
    assert line("confirm", "plain", noon, summary="x") == "Should I go ahead with x?"
    assert "theatrical" in dj_voice("escanor", noon) and dj_voice("plain") == "a warm, witty radio DJ"

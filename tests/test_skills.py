import pytest
from escanor import config
from escanor.app import build_app, build_model
from escanor.core.events import ToolFinished, TurnDone
from escanor.models.base import ModelResponse, ToolCall
from escanor.models.mock_provider import MockModel
from escanor.skills.loader import discover, skill_index_section, split_frontmatter
from escanor.skills.tools import ReadSkillFileTool, UseSkillTool


def _write_skill(root, name, description_block, body="Do the thing.", files=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n{description_block}\n---\n\n{body}\n")
    for relative, text in (files or {}).items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return folder


@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "skills"
    _write_skill(root, "learn-anything", "description: Tutor the user.\nuser-invocable: true",
                 files={"references/socratic.md": "Ask one question at a time.", "templates/log.md": "# Log"})
    _write_skill(root, "remotion", "description: >-\n  Make videos\n  with React.")
    _write_skill(root, "kb", "description: Work knowledgebase.")
    return root


def test_should_fold_block_scalar_when_parsing_frontmatter():
    fields, body = split_frontmatter("---\nname: x\ndescription: >-\n  line one\n  line two\n---\nBody\n")
    assert fields == {"name": "x", "description": "line one line two"}
    assert body.strip() == "Body"


def test_should_return_text_unchanged_when_no_frontmatter():
    assert split_frontmatter("# Just text") == ({}, "# Just text")


def test_should_filter_by_allow_list_when_discovering(skills_dir):
    skills = discover([skills_dir], allow=["learn-anything", "remotion"])
    assert sorted(skills) == ["learn-anything", "remotion"]
    assert skills["remotion"].description == "Make videos with React."


def test_should_prefer_first_directory_when_names_clash(tmp_path, skills_dir):
    mine = tmp_path / "mine"
    _write_skill(mine, "learn-anything", "description: My own version.")
    skills = discover([mine, skills_dir, tmp_path / "missing"])
    assert skills["learn-anything"].description == "My own version."


def test_should_list_files_and_strip_frontmatter_when_skill_used(skills_dir):
    tool = UseSkillTool(discover([skills_dir]))
    output = tool.run(name="learn-anything").output
    assert "Do the thing." in output and "name: learn-anything" not in output
    assert "- references/socratic.md" in output and "- templates/log.md" in output
    assert tool.run(name="nope").ok is False


def test_should_read_file_inside_skill_and_refuse_escape(skills_dir, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    tool = ReadSkillFileTool(discover([skills_dir]))
    assert tool.run(skill="learn-anything", path="references/socratic.md").output == "Ask one question at a time."
    escape = tool.run(skill="learn-anything", path="../../secret.txt")
    assert escape.ok is False and "outside" in escape.output
    missing = tool.run(skill="learn-anything", path="nope.md")
    assert missing.ok is False and "references/socratic.md" in missing.output


def test_should_list_name_and_description_when_building_index(skills_dir):
    section = skill_index_section(discover([skills_dir], allow=["learn-anything"]))
    assert "- learn-anything: Tutor the user." in section
    assert skill_index_section({}) == ""


async def test_should_load_skill_through_agent_when_model_asks(skills_dir, store):
    settings = config.load_settings()
    settings["skills"] = {"dirs": [str(skills_dir)], "allow": ["learn-anything"]}
    settings["brain"]["embedder"] = "none"
    model, spec = build_model("mock", settings)
    app = build_app(settings, model, spec)
    agent, skills = app.agent, app.skills
    scripted = MockModel(script=[
        ModelResponse(tool_calls=[ToolCall("c1", "use_skill", {"name": "learn-anything"})]),
        ModelResponse(text="Let's begin. What do you already know?"),
    ])
    agent.set_model(scripted, "mock:scripted")
    events = [e async for e in agent.run_turn(store.create(), "teach me transformers")]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.result.ok and "Do the thing." in finished.result.output
    assert isinstance(events[-1], TurnDone)
    system = scripted.calls[0]["messages"][0]["content"]
    assert "- learn-anything: Tutor the user." in system and "<untrusted" in system
    assert "kb" not in skills

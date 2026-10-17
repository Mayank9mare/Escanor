from escanor import config
from escanor.cli import Repl, build_model, main
from escanor.core.agent import Agent


def _repl(store):
    settings = config.load_settings()
    model, spec = build_model("mock", settings)
    return Repl(Agent(model, spec), store, store.create(), settings)


def test_should_switch_model_when_alias_valid(store):
    repl = _repl(store)
    settings_alias_spec = "mock:other"
    repl.handle_command(f"/model {settings_alias_spec}")
    assert repl.agent.model_spec == "mock:other"


def test_should_keep_model_and_explain_when_key_missing(store, capsys):
    repl = _repl(store)
    assert repl.handle_command("/model sonnet") is True
    assert repl.agent.model_spec == "mock:mock-model"
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().out


def test_should_start_new_conversation_when_new_command(store):
    repl = _repl(store)
    first = repl.session.id
    repl.handle_command("/new")
    assert repl.session.id != first


def test_should_exit_when_quit_command(store):
    assert _repl(store).handle_command("/quit") is False


def test_should_return_error_code_when_default_model_has_no_key(capsys, monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda path=None: None)  # ignore any real .env
    assert main([]) == 2
    assert "escanor --model mock" in capsys.readouterr().err

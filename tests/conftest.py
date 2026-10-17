import pytest
from escanor.core.session import SessionStore


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.escanor and telemetry DB, and no real keys."""
    monkeypatch.setenv("ESCANOR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ESCANOR_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import escanor.telemetry as telemetry
    monkeypatch.setattr(telemetry, "_store", None)
    yield


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "escanor.db")

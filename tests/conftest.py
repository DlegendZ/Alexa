from __future__ import annotations

import pytest

from sunday import config


@pytest.fixture(autouse=True, scope="session")
def private_sunday_home(tmp_path_factory):
    """A throwaway `SUNDAY_HOME` for the whole run, so the suite cannot write
    into the user's own data directory.

    `sandbox` and `cfg` below isolate the file roots and the config, and the
    store was left alone -- so a `Runtime(cfg, agent=agent)` built without an
    explicit `memory=` fell through to `LongTermMemory()`, which opens
    `config.MEMORY_DIR`. Two call sites did exactly that, and a test run filed
    turns like "read my .env / ok" into real long-term memory, where the next
    real session recalled them. The log went to the real log directory for the
    same reason.

    Autouse and session-scoped because the rule is about the run, not about
    the tests that remember to ask. Per-test fixtures that patch these again
    (`tests/test_settings.py`) still win, being function-scoped.

    `MODEL_DIR` is deliberately not redirected. The voice models are a ~1 GB
    read-only download that every voice test skips without, and pointing it at
    an empty temp directory would turn "the models are installed" into a
    silent skip of the tests that need them.
    """
    home = tmp_path_factory.mktemp("sunday-home")
    patch = pytest.MonkeyPatch()
    patch.setenv("SUNDAY_HOME", str(home))
    patch.setattr(config, "SUNDAY_HOME", home)
    patch.setattr(config, "MEMORY_DIR", home / "memory")
    patch.setattr(config, "LOG_DIR", home / "logs")
    patch.setattr(config, "HANDSHAKE_PATH", home / "handshake.json")
    patch.setattr(config, "GOOGLE_TOKEN_PATH", home / "google_token.json")
    patch.setattr(config, "CREDENTIALS_PATH", home / "credentials.json")
    yield home
    patch.undo()


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A config whose only file root is a temp directory, so the sandbox tests
    never touch the real one."""
    root = tmp_path / "root"
    root.mkdir()
    cfg = config.Config()
    cfg.files.roots = [str(root)]
    cfg.files.max_read_bytes = 200
    monkeypatch.setattr(config, "_cache", cfg)
    return root


@pytest.fixture
def cfg(monkeypatch):
    """A default config, isolated from whatever config.toml says."""
    fresh = config.Config()
    monkeypatch.setattr(config, "_cache", fresh)
    return fresh

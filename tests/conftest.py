from __future__ import annotations

import pytest

from sunday import config


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

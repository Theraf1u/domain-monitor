"""Migration 2.0's persistent server-URL override (spec 3.4): the one
file the agent ever writes to record a successful migration, since it
can't touch the host's .env from inside its container."""
from __future__ import annotations

import json
import os

from app import runtime_config


def test_no_override_returns_none(tmp_path):
    assert runtime_config.load_override(str(tmp_path)) is None


def test_set_and_load_override(tmp_path):
    runtime_config.set_override(str(tmp_path), "http://new-server.example")
    assert runtime_config.load_override(str(tmp_path)) == "http://new-server.example"


def test_effective_server_url_prefers_override(tmp_path):
    assert runtime_config.effective_server_url("http://configured.example", str(tmp_path)) == "http://configured.example"
    runtime_config.set_override(str(tmp_path), "http://migrated.example")
    assert runtime_config.effective_server_url("http://configured.example", str(tmp_path)) == "http://migrated.example"


def test_clear_override_falls_back_to_configured(tmp_path):
    runtime_config.set_override(str(tmp_path), "http://migrated.example")
    runtime_config.set_override(str(tmp_path), None)
    assert runtime_config.load_override(str(tmp_path)) is None
    assert runtime_config.effective_server_url("http://configured.example", str(tmp_path)) == "http://configured.example"


def test_clearing_a_nonexistent_override_does_not_raise(tmp_path):
    runtime_config.set_override(str(tmp_path), None)  # no file exists yet


def test_corrupt_file_is_treated_as_no_override(tmp_path):
    path = os.path.join(str(tmp_path), "runtime.json")
    with open(path, "w") as fh:
        fh.write("{not valid json")
    assert runtime_config.load_override(str(tmp_path)) is None


def test_write_is_atomic_no_tmp_file_left_behind(tmp_path):
    runtime_config.set_override(str(tmp_path), "http://new.example")
    entries = os.listdir(str(tmp_path))
    assert entries == ["runtime.json"]
    with open(os.path.join(str(tmp_path), "runtime.json")) as fh:
        assert json.load(fh) == {"server_url_override": "http://new.example"}

"""HP-11: migrate_data.py CLI via SUBPROCESS (in-process imports would clash
with the conftest module state - the CLI sets CC_COMMUNICATE_DATA_DIR itself)."""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, "tools", "migrate_data.py")


def _run(root, *extra):
    return subprocess.run(
        [sys.executable, TOOL, "--data-root", str(root)] + list(extra),
        capture_output=True, text=True, cwd=REPO)


def test_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text('{"s1": {"pid": 1}}', encoding="utf-8")
    r = _run(root, "--dry-run")
    assert r.returncode == 0
    assert "OK" in r.stdout
    assert "WOULD" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8")) == \
        {"s1": {"pid": 1}}   # untouched


def test_migrates_flat_registries(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text('{"s1": {"pid": 1}}', encoding="utf-8")
    (server / "alive_conversations.json").write_text(
        '[["a", "b", {}]]', encoding="utf-8")
    (server / "ack_timestamps.json").write_text('{"s1": 42}', encoding="utf-8")
    (server / "machine_identity.json").write_text(
        '{"type": "win-host"}', encoding="utf-8")
    r = _run(root)
    assert r.returncode == 0 and "OK" in r.stdout
    assert "migrated 4 file(s)" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "sessions": {"s1": {"pid": 1}}}
    assert json.loads((server / "alive_conversations.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "conversations": [["a", "b", {}]]}
    assert json.loads((server / "ack_timestamps.json").read_text(encoding="utf-8")) == \
        {"schema_version": 1, "ack_timestamps": {"s1": 42}}
    assert json.loads((server / "machine_identity.json").read_text(encoding="utf-8"))["schema_version"] == 1
    # idempotent: second run is a no-op
    r2 = _run(root)
    assert r2.returncode == 0 and "migrated 0 file(s)" in r2.stdout


def test_newer_schema_refused_file_untouched(tmp_path):
    root = tmp_path / "data"
    server = root / "server"
    server.mkdir(parents=True)
    (server / "sessions.json").write_text(
        '{"schema_version": 2, "sessions": {"s1": {}}}', encoding="utf-8")
    r = _run(root)
    assert r.returncode == 1
    assert "REFUSED" in r.stdout
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8"))["schema_version"] == 2
    # dry-run also refuses (exit 1, nothing written)
    r2 = _run(root, "--dry-run")
    assert r2.returncode == 1
    assert json.loads((server / "sessions.json").read_text(encoding="utf-8"))["schema_version"] == 2

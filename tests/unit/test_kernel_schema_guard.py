"""HP-11: kernel loaders refuse newer-format state files + dual-read the
wrapped v1 registries (HP-01 dual-reader precedent)."""
import json
import logging
import os


def test_load_sessions_skips_newer_schema(server, caplog):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "sessions": {"s1": {}}}, f)
    with caplog.at_level(logging.WARNING, logger="cc-communicate.kernel"):
        k._load_sessions()
    assert k.sessions == {}
    assert any("schema_version 2" in r.message for r in caplog.records)
    with open(server.paths.SESSIONS_FILE, encoding="utf-8") as f:
        assert json.load(f)["schema_version"] == 2   # file untouched


def test_load_sessions_dual_read_legacy_and_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"s1": {"pid": 1}}, f)             # legacy flat
    k._load_sessions()
    assert "s1" in k.sessions
    k.sessions.clear()
    with open(server.paths.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "sessions": {"s2": {"pid": 2}}}, f)
    k._load_sessions()
    assert "s2" in k.sessions


def test_save_sessions_emits_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k.sessions.update({"s1": {"pid": 1}})
    k._save_sessions()
    with open(server.paths.SESSIONS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "sessions": {"s1": {"pid": 1}}}


def test_load_alive_convs_dual_read(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.ALIVE_CONVS_FILE, "w", encoding="utf-8") as f:
        json.dump([["a", "b", {"established_at": 1.0}]], f)
    k._load_alive_convs()
    assert ("a", "b") in k.alive_conversations
    k.alive_conversations.clear()
    with open(server.paths.ALIVE_CONVS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1,
                   "conversations": [["c", "d", {}]]}, f)
    k._load_alive_convs()
    assert ("c", "d") in k.alive_conversations


def test_load_ack_timestamps_dual_read(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.ACK_TIMESTAMPS_FILE, "w", encoding="utf-8") as f:
        json.dump({"s1": 42}, f)
    k._load_ack_timestamps()
    assert k.acked_timestamps["s1"] == 42
    k.acked_timestamps.clear()
    with open(server.paths.ACK_TIMESTAMPS_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 1, "ack_timestamps": {"s2": 7}}, f)
    k._load_ack_timestamps()
    assert k.acked_timestamps["s2"] == 7


def test_save_ack_timestamps_emits_wrapped(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    k.acked_timestamps["s1"] = 42
    k._save_ack_timestamps()
    with open(server.paths.ACK_TIMESTAMPS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "ack_timestamps": {"s1": 42}}


def test_upload_ack_timestamp_emits_wrapped(server):
    ka = server.kernel_api
    server.paths.ensure_runtime_dirs()
    ka.upload_ack_timestamp({}, "s1", 42)
    with open(server.paths.ACK_TIMESTAMPS_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "ack_timestamps": {"s1": 42}}


def test_load_message_sequence_skips_newer(server, caplog):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    with open(server.paths.MESSAGE_SEQUENCE_FILE, "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2, "last_allocated": 99}, f)
    with caplog.at_level(logging.WARNING, logger="cc-communicate.kernel"):
        k._load_message_sequence()
    assert k.message_sequence["last_allocated"] == 0   # default, not 99

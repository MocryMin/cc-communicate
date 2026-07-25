"""Carry-forward：end 事件回放（完整 session 生命周期 start -> end -> restart）。"""
import json
import os


def _write_event(ctrl_dir, name, ev):
    with open(os.path.join(str(ctrl_dir), name), "w", encoding="utf-8") as f:
        json.dump(ev, f)


def test_end_event_replay_lifecycle(server):
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    start = {"event": "start", "event_ts": 1000, "session_id": "sess-1",
             "pid": 4242, "cwd": "/tmp/x", "start_time": "2026-07-24T10:00:00",
             "source": "hook"}
    end = {"event": "end", "event_ts": 2000, "session_id": "sess-1", "pid": 4242}
    _write_event(server.paths.SESSION_CTRL_DIR, "0000000001000_start.json", start)
    k._seen_events.clear(); k.sessions.clear(); k.alive_sessions.clear()
    k.process_session_ctrl_event()
    assert "sess-1" in k.alive_sessions
    _write_event(server.paths.SESSION_CTRL_DIR, "0000000002000_end.json", end)
    k.process_session_ctrl_event()
    assert "sess-1" not in k.alive_sessions
    assert k.sessions["sess-1"]["ended_at"] == 2000
    # restart：清空内存，从 sessions.json 恢复，ended_at 持久
    k.sessions.clear(); k.alive_sessions.clear(); k._seen_events.clear()
    k._load_sessions()
    assert k.sessions["sess-1"]["ended_at"] == 2000
    assert "sess-1" not in k.alive_sessions

"""T30 regression: SessionStart double-fire can leave a dead pid as primary;
check_alive must fall back across known_pids (every pid ever recorded for the
sid) instead of trusting the last event alone. Without this, check_alive
returns 0 for a LIVE session and connect's revive path spawns a second window.

AR-03: the known_pids bound-trim must trim by INSERTION ORDER, not by
start_time value (None start_time made sorted() raise TypeError on replay of
9+ events)."""
import os

import pytest


def _alive_entry(server, monkeypatch, pid, start):
    """Simulate what kernel._handle_start does for one start event."""
    k = server.kernel
    monkeypatch.setattr(k, "parse_start_time", lambda _s: start)
    ev = {"event": "start", "event_ts": int(start * 1000),
          "session_id": "sess-t30", "pid": pid, "cwd": "/tmp/x",
          "start_time": "unused", "source": "startup"}
    k._handle_start(ev, "sess-t30")


def test_fallback_to_known_good_pid(server, monkeypatch):
    """Good pid first, dead pid second (the live bug's order): check_alive
    must NOT be fooled by the last event - it falls back to the good pid."""
    ka = server.kernel_api
    fake = {111: 1000.0, 222: None}  # 111 alive, 222 dead
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    _alive_entry(server, monkeypatch, 111, 1000.0)
    _alive_entry(server, monkeypatch, 222, 2000.0)
    alive = server.kernel.alive_sessions
    assert alive["sess-t30"]["pid"] == 222  # primary is the (bad) last event
    assert ka.check_alive(alive, "sess-t30") == 1
    assert alive["sess-t30"]["pid"] == 111  # promoted to the live pid
    assert 222 not in alive["sess-t30"]["known_pids"]  # dead pruned


def test_dead_pid_first_then_good(server, monkeypatch):
    """The resume-child order: dead first, good second - already worked, must
    keep working."""
    ka = server.kernel_api
    fake = {111: 1000.0, 222: None}
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    _alive_entry(server, monkeypatch, 222, 2000.0)
    _alive_entry(server, monkeypatch, 111, 1000.0)
    alive = server.kernel.alive_sessions
    assert ka.check_alive(alive, "sess-t30") == 1


def test_all_pids_dead_returns_0_and_pops(server, monkeypatch):
    """Genuinely dead session: no candidate matches -> 0 and the entry is
    popped (evoke is then the correct recovery)."""
    ka = server.kernel_api
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: None)
    _alive_entry(server, monkeypatch, 111, 1000.0)
    _alive_entry(server, monkeypatch, 222, 2000.0)
    alive = server.kernel.alive_sessions
    assert ka.check_alive(alive, "sess-t30") == 0
    assert "sess-t30" not in alive


def test_start_time_mismatch_rejected(server, monkeypatch):
    """A live pid whose start_time does not match the recorded one is treated
    as a different process (pid reuse) - never promoted."""
    ka = server.kernel_api
    fake = {111: 9999.0}  # alive, but a DIFFERENT incarnation
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    _alive_entry(server, monkeypatch, 111, 1000.0)
    alive = server.kernel.alive_sessions
    assert ka.check_alive(alive, "sess-t30") == 0
    assert "sess-t30" not in alive


def test_known_pids_bounded(server, monkeypatch):
    """known_pids never grows past 8 entries."""
    k = server.kernel
    monkeypatch.setattr(k, "parse_start_time", lambda _s: 0.0)
    ev = {"event": "start", "event_ts": 0, "session_id": "sess-t30",
          "pid": 1, "cwd": "/tmp/x", "start_time": "unused", "source": "startup"}
    for i in range(12):
        ev = dict(ev, pid=i + 100, event_ts=i)
        k._handle_start(ev, "sess-t30")
    assert len(server.kernel.alive_sessions["sess-t30"]["known_pids"]) <= 8


# ---------- AR-03: bound-trim must not sort on start_time ----------
# The old sorted(known, key=known.get)[:-8] raised TypeError once >8 events
# replayed with None/mixed start_time (None vs float comparison). Trim is by
# INSERTION ORDER; start_time serves only PID-reuse validation. Tests use the
# REAL parse_start_time (None for missing, float for valid ISO).


def test_known_pids_all_none_no_crash(server):
    """9+ SessionStart events with start_time=None -> no crash (all-None
    sorted() raised TypeError too: None < None), bounded, and the 8 most
    recent events survive (insertion-order trim semantics)."""
    k = server.kernel
    for i in range(12):
        ev = {"event": "start", "event_ts": i, "session_id": "sess-ar03",
              "pid": 100 + i, "cwd": "/tmp/x", "start_time": None,
              "source": "startup"}
        k._handle_start(ev, "sess-ar03")
    known = server.kernel.alive_sessions["sess-ar03"]["known_pids"]
    assert len(known) <= 8
    assert list(known) == list(range(104, 112))  # oldest 4 trimmed, order kept


def test_known_pids_mixed_none_float_no_crash(server):
    """9+ events mixing None and float start_time -> no crash, bounded (the
    old code compared None against float and raised)."""
    k = server.kernel
    for i in range(12):
        start = None if i % 2 else "2026-08-03T00:00:00Z"
        ev = {"event": "start", "event_ts": i, "session_id": "sess-ar03",
              "pid": 200 + i, "cwd": "/tmp/x", "start_time": start,
              "source": "startup"}
        k._handle_start(ev, "sess-ar03")
    known = server.kernel.alive_sessions["sess-ar03"]["known_pids"]
    assert len(known) <= 8
    assert list(known) == list(range(204, 212))


def test_known_pids_pid_dup_bounded_check_alive_ok(server, monkeypatch):
    """PID re-starting multiple times: bounded, and check_alive keeps working
    (no regression from the trim change)."""
    ka = server.kernel_api
    k = server.kernel
    monkeypatch.setattr(k, "parse_start_time", lambda _s: 1000.0)
    fake = {300: 1000.0}  # only pid 300 is alive
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    # 10 distinct pids with 300 starting last (survives the trim) + dups
    pids = [301, 302, 303, 304, 305, 306, 307, 308, 309, 300, 300, 300]
    for i, pid in enumerate(pids):
        ev = {"event": "start", "event_ts": i, "session_id": "sess-ar03",
              "pid": pid, "cwd": "/tmp/x", "start_time": "unused",
              "source": "startup"}
        k._handle_start(ev, "sess-ar03")
    alive = server.kernel.alive_sessions
    known = alive["sess-ar03"]["known_pids"]
    assert len(known) <= 8
    assert alive["sess-ar03"]["pid"] == 300  # primary = last event
    assert ka.check_alive(alive, "sess-ar03") == 1  # known_pids fallback works
    assert alive["sess-ar03"]["pid"] == 300


def test_known_pids_old_log_replay_no_crash(server):
    """Kernel restart replays persisted session_ctrl start events via
    process_session_ctrl_event (the real restart path): 9+ events with
    missing/mixed start_time -> no crash, bounded."""
    import json
    k = server.kernel
    server.paths.ensure_runtime_dirs()
    sdir = server.paths.SESSION_CTRL_DIR
    for i in range(10):
        ev = {"event": "start", "event_ts": 1000 + i,
              "session_id": "sess-ar03", "pid": 400 + i, "cwd": "/tmp/x",
              "start_time": None if i % 2 else "2026-08-03T01:00:00Z",
              "source": "startup"}
        with open(os.path.join(sdir, "start_%d_%d.json" % (1000 + i, i)),
                  "w", encoding="utf-8") as f:
            json.dump(ev, f)
    assert k.process_session_ctrl_event() is True
    known = k.alive_sessions["sess-ar03"]["known_pids"]
    assert len(known) <= 8
    assert k.sessions["sess-ar03"]["pid"] == 409  # last event processed


# ---------- T35: session_by_pid known_pids fallback (the my_session_id hole) ----------


def test_session_by_pid_falls_back_to_known_pids(server, monkeypatch):
    """Primary sessions[sid].pid is the dead last-write; the REAL pid resolves
    via the known_pids fallback (T35 - the my_session_id hole)."""
    ka = server.kernel_api
    fake = {111: 1000.0, 222: None}  # 111 alive, 222 dead
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    _alive_entry(server, monkeypatch, 111, 1000.0)
    _alive_entry(server, monkeypatch, 222, 2000.0)  # last write = dead 222 (primary)
    assert ka.session_by_pid(server.kernel.sessions,
                             server.kernel.alive_sessions, 111) == "sess-t30"
    assert ka.session_by_pid(server.kernel.sessions,
                             server.kernel.alive_sessions, 222) == "sess-t30"  # primary raw match (unchanged hot path)


def test_session_by_pid_unknown_pid_none(server, monkeypatch):
    ka = server.kernel_api
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: None)
    _alive_entry(server, monkeypatch, 111, 1000.0)
    assert ka.session_by_pid(server.kernel.sessions,
                             server.kernel.alive_sessions, 999999) is None


def test_session_by_pid_primary_unchanged(server, monkeypatch):
    """No known_pids (pre-T30 style entry) -> primary scan only, same as before."""
    ka = server.kernel_api
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: 1000.0)
    k = server.kernel
    ev = {"event": "start", "event_ts": 1000000, "session_id": "sess-t30",
          "pid": 111, "cwd": "/tmp/x", "start_time": "unused",
          "source": "startup"}
    monkeypatch.setattr(k, "parse_start_time", lambda _s: 1000.0)
    k._handle_start(ev, "sess-t30")
    k.alive_sessions["sess-t30"].pop("known_pids", None)  # simulate pre-T30 entry
    assert ka.session_by_pid(k.sessions, k.alive_sessions, 111) == "sess-t30"
    assert ka.session_by_pid(k.sessions, k.alive_sessions, 999) is None


def test_session_by_pid_never_resolves_other_sid(server, monkeypatch):
    """A known pid of sid A never resolves to sid B (the T35 invariant)."""
    ka = server.kernel_api
    fake = {111: 1000.0, 333: 3000.0}
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: fake.get(pid))
    k = server.kernel
    monkeypatch.setattr(k, "parse_start_time", lambda _s: 0.0)
    ev_a = {"event": "start", "event_ts": 1, "session_id": "sess-a",
            "pid": 111, "cwd": "/tmp/x", "start_time": "unused", "source": "startup"}
    ev_b = {"event": "start", "event_ts": 2, "session_id": "sess-b",
            "pid": 333, "cwd": "/tmp/x", "start_time": "unused", "source": "startup"}
    k._handle_start(ev_a, "sess-a")
    k._handle_start(ev_b, "sess-b")
    assert ka.session_by_pid(k.sessions, k.alive_sessions, 111) == "sess-a"
    assert ka.session_by_pid(k.sessions, k.alive_sessions, 333) == "sess-b"

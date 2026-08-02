"""HP-08 ride-along: proc.pid_matches is the shared liveness rule
(previously duplicated as check_alive._match and session_by_pid._pid_live)."""
import pytest
import proc


def test_pid_matches_unknown(server):
    assert proc.pid_matches(None, 1.0) is None
    assert proc.pid_matches(123, None) is None


def test_pid_matches_dead(server, monkeypatch):
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: None)
    assert proc.pid_matches(123, 1.0) is False


def test_pid_matches_alive(server, monkeypatch):
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: 1000.0)
    assert proc.pid_matches(123, 1000.0) is True


def test_pid_matches_start_time_mismatch(server, monkeypatch):
    """A live pid with a different start time is a DIFFERENT incarnation
    (pid reuse) - never a match."""
    monkeypatch.setattr(server.proc, "proc_start_time", lambda pid: 9999.0)
    assert proc.pid_matches(123, 1000.0) is False

"""HP-05 (D9): info.json lifecycle - activate / get / deactivate / conflict."""
import json
import os


def _conn_id(n):
    return f"c{n:031d}"  # fits the [A-Za-z0-9-] charset, 32 chars


def test_activate_writes_info_json(server):
    ka = server.kernel_api
    convs = {}
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    assert r["activated"] is True and r["reused"] is False
    assert ("alice", "bob") in convs  # registered
    info = ka.get_connection_info("alice", "bob")
    assert info["connection_id"] == _conn_id(1)
    assert info["status"] == "active" and info["schema_version"] == 1
    assert info["sid_a"] == "alice" and info["sid_b"] == "bob"
    assert isinstance(info["established_at_ms"], int)
    # order-independent path
    assert ka.get_connection_info("bob", "alice") == info


def test_activate_same_id_reuses(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    assert r["activated"] is True and r["reused"] is True


def test_activate_conflict_different_id(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.activate_connection(convs, "alice", "bob", _conn_id(2))
    assert r["activated"] is False and r["reason"] == "conflict"
    assert r["current_connection_id"] == _conn_id(1)
    # no double registration / no file overwrite
    assert ka.get_connection_info("alice", "bob")["connection_id"] == _conn_id(1)


def test_deactivate_marks_closed(server):
    ka = server.kernel_api
    convs = {}
    ka.activate_connection(convs, "alice", "bob", _conn_id(1))
    r = ka.deactivate_connection(convs, "alice", "bob")
    assert r == {"closed": True}
    assert ("alice", "bob") not in convs  # unregistered
    info = ka.get_connection_info("alice", "bob")
    assert info["status"] == "closed" and info["connection_id"] == _conn_id(1)
    assert isinstance(info.get("closed_at_ms"), int)


def test_get_connection_info_absent(server):
    ka = server.kernel_api
    assert ka.get_connection_info("alice", "bob") is None


def test_info_path_validates(server):
    d = server.conversations.info_path("alice", "bob")
    assert d.endswith("info.json")
    assert os.path.dirname(d) == server.conversations.conv_dir("alice", "bob")

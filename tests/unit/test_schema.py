"""HP-11: data-root schema conventions - check/stamp/wrap/layout (D2)."""
import json
import os


def test_schema_too_new_matrix(server):
    s = server.schema
    assert s.schema_too_new(None) is False
    assert s.schema_too_new("x") is False
    assert s.schema_too_new({}) is False
    assert s.schema_too_new({"schema_version": 1}) is False
    assert s.schema_too_new({"schema_version": 2}) is True
    assert s.schema_too_new({"schema_version": 0}) is False
    assert s.schema_too_new({"schema_version": -1}) is False
    assert s.schema_too_new({"schema_version": "2"}) is False   # non-int tolerated
    assert s.schema_too_new({"schema_version": True}) is False  # bool is not a version


def test_unwrap(server):
    s = server.schema
    assert s.unwrap({"schema_version": 1, "sessions": {"s1": {}}},
                    "sessions") == {"s1": {}}
    assert s.unwrap({"schema_version": 1,
                     "conversations": [["a", "b", {}]]},
                    "conversations") == [["a", "b", {}]]
    assert s.unwrap({"schema_version": 1}, "sessions") is None  # wrapped, key missing
    assert s.unwrap({"s1": {}}, "sessions") == {"s1": {}}       # legacy passthrough
    assert s.unwrap(["a"], "conversations") == ["a"]            # legacy list passthrough
    assert s.unwrap(None, "sessions") is None


def test_stamp_and_wrap(server):
    s = server.schema
    os.makedirs(server.paths.SERVER_DATA_DIR, exist_ok=True)
    # stamp: machine_identity-style flat dict (extra key ignored by its loader)
    p = os.path.join(server.paths.SERVER_DATA_DIR, "machine_identity.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"type": "win-host"}, f)
    assert s.needs_stamp(p) is True
    assert s.stamp_v1(p) is True
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["schema_version"] == 1
    assert s.stamp_v1(p) is False          # already stamped -> no-op
    assert s.needs_stamp(p) is False
    # wrap: flat sessions dict
    p2 = os.path.join(server.paths.SERVER_DATA_DIR, "sessions.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"s1": {"pid": 1}}, f)
    assert s.needs_wrap(p2, "sessions") is True
    assert s.wrap_v1(p2, "sessions") is True
    with open(p2, encoding="utf-8") as f:
        assert json.load(f) == {"schema_version": 1,
                                "sessions": {"s1": {"pid": 1}}}
    assert s.wrap_v1(p2, "sessions") is False  # already wrapped -> no-op
    assert s.needs_wrap(p2, "sessions") is False
    # wrap: bare list (alive_conversations)
    p3 = os.path.join(server.paths.SERVER_DATA_DIR, "alive_conversations.json")
    with open(p3, "w", encoding="utf-8") as f:
        json.dump([["a", "b", {}]], f)
    assert s.wrap_v1(p3, "conversations") is True
    with open(p3, encoding="utf-8") as f:
        assert json.load(f)["conversations"] == [["a", "b", {}]]


def test_validate_layout(server):
    s = server.schema
    root = str(server.data_root)
    # fresh root: dir warnings, no errors
    errors, warnings = s.validate_layout(root)
    assert errors == []
    assert any("missing runtime dir" in w for w in warnings)
    # newer file -> error (REFUSED)
    os.makedirs(server.paths.SERVER_DATA_DIR, exist_ok=True)
    with open(os.path.join(server.paths.SERVER_DATA_DIR, "sessions.json"),
              "w", encoding="utf-8") as f:
        json.dump({"schema_version": 2}, f)
    errors, warnings = s.validate_layout(root)
    assert any("REFUSED" in e for e in errors)

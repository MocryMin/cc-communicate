"""HP-07: kernel returns are structured dicts - no string results for control flow."""
from result import Code  # noqa: F401 - keep import for parity of thought


def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_send_message_structured(server):
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "hello")
    assert r["sent"] is True
    assert isinstance(r["ts"], int) and r["message_id"]
    assert r["correlation_id"] is None


def test_send_unregistered_structured(server):
    ka = server.kernel_api
    r = ka.send_message({}, _seq_state(), "store-test", "alice", "bob", "hi")
    assert r == {"sent": False, "reason": "connection not registered"}


def test_withdraw_structured(server):
    ka = server.kernel_api
    convs = {}
    ka.register_conversation(convs, "alice", "bob")
    assert ka.withdraw(convs, "alice", "bob", init_connect=1) == \
        {"withdrawn": True, "detail": "conversation withdrawn"}
    r = ka.withdraw(convs, "alice", "bob", init_connect=1)
    assert r["withdrawn"] is False


def test_evoke_structured(server):
    ka = server.kernel_api
    sessions = {"s1": {"cwd": "/tmp", "session_id": "s1"}}
    assert ka.evoke({}, "nope") == {"evoked": False, "reason": "session unknown"}


def test_register_unregister_structured(server):
    ka = server.kernel_api
    convs = {}
    assert ka.register_conversation(convs, "alice", "bob") == {"ok": True}
    assert (("alice", "bob") in convs)
    assert ka.unregister_conversation(convs, "alice", "bob") == {"ok": True}
    assert (("alice", "bob") not in convs)

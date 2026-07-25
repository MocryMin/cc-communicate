def _seq_state():
    return {"schema_version": 1, "store_id": "store-test", "last_allocated": 0}


def test_cancel_listen_redelivers(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    ka.send_message(convs, _seq_state(), "store-test", "alice", "bob", "m1")

    # 第一次 listen：CC peek 到消息（但在确认前被 cancel）
    res1 = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res1["messages"]] == ["m1"]

    # CC 取消后未推进 watermark，以相同 acked_ts=0 重 listen -> 消息重投，不丢
    res2 = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res2["messages"]] == ["m1"]

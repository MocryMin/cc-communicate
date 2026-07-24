import os


def test_register_send_listen_ack_roundtrip(server):
    ka = server.kernel_api
    convs, acked = {}, {}
    ka.register_conversation(convs, "alice", "bob")
    r = ka.send_message(convs, "alice", "bob", "hello")
    assert r.startswith("message_sent at ")

    # bob 以 acked_ts=0 listen -> peek 到消息但不归档
    res = ka.listen_scan(acked, "bob", 0)
    assert [m["message"] for m in res["messages"]] == ["hello"]
    assert res["messages"][0]["from_id"] == "alice"
    wm = res["watermark"]

    # bob 确认 watermark -> 再次 listen 归档该消息且无新消息
    res2 = ka.listen_scan(acked, "bob", wm)
    assert res2["messages"] == []

    # 消息已从 pipe/ 移到 log/
    d = server.conversations.conv_dir("alice", "bob")
    assert os.listdir(os.path.join(d, "pipe")) == []
    assert len(os.listdir(os.path.join(d, "log"))) == 1


def test_send_requires_registration(server):
    ka = server.kernel_api
    convs = {}
    r = ka.send_message(convs, "alice", "bob", "hi")
    assert r == "failed, connection not registered"

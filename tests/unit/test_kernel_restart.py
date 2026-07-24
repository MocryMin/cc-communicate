def test_kernel_restart_recovers_state(server):
    k = server.kernel
    # 与真实 kernel.main() 一致：任何 _save_* 之前先建运行目录（否则
    # SERVER_DATA_DIR 不存在，_atomic_write_json 会 FileNotFoundError）。
    server.paths.ensure_runtime_dirs()
    k.sessions.update({"s1": {"session_id": "s1", "pid": 123}})
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    k.acked_timestamps["s1"] = 42
    k._save_sessions()
    k._save_alive_convs()
    k._save_ack_timestamps()

    # 模拟重启：清空内存态，从磁盘恢复
    k.sessions.clear()
    k.alive_conversations.clear()
    k.acked_timestamps.clear()
    k._load_sessions()
    k._load_alive_convs()
    k._load_ack_timestamps()

    assert "s1" in k.sessions
    assert ("a", "b") in k.alive_conversations
    assert k.acked_timestamps["s1"] == 42

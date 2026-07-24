def test_server_fixture_isolates_data_root(server):
    # fixture 把 DATA_DIR 绑到本测试独立 tmp_path，且各模块绑定一致
    assert server.paths.DATA_DIR == __import__("os").path.abspath(str(server.data_root))
    # kernel_api 与 conversations 的 CONVERSATIONS_DIR 都落在同一隔离 root 下
    assert server.kernel_api.CONVERSATIONS_DIR == server.conversations.CONVERSATIONS_DIR
    assert str(server.data_root) in server.kernel_api.CONVERSATIONS_DIR
    # 隔离 root 初始为空（不污染真实 plugin data/）
    assert list(server.data_root.iterdir()) == []

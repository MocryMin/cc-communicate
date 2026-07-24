import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# 被测 realm 的 server 目录。parity gate（tests/parity）证明两树等价，故测一个即
# 覆盖两个；设 CC_TEST_SERVER_DIR 可对另一 realm 重跑。
SERVER_DIR = Path(os.environ.get(
    "CC_TEST_SERVER_DIR",
    REPO_ROOT / "v2_win" / "cc-communicate" / "server",
)).resolve()
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """把 cc-communicate server 绑定到本测试独立的 tmp data root。

    设 CC_COMMUNICATE_DATA_DIR 后按依赖序 reload 路径相关模块，使 import 时绑定
    的路径常量（DATA_DIR/CONVERSATIONS_DIR/...）重新解析到 tmp_path。返回命名空间
    暴露重载后的模块与该 root。kernel_api 函数以 state dict 为首参，直接调用即可。"""
    monkeypatch.setenv("CC_COMMUNICATE_DATA_DIR", str(tmp_path))
    mods = {}
    for name in ("paths", "conversations", "spawn", "kernel_api", "kernel"):
        mods[name] = importlib.reload(importlib.import_module(name))
    return SimpleNamespace(data_root=tmp_path, **mods)

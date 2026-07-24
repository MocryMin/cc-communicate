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

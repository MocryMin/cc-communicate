"""HP-13-B: 若 v2_win 与 v2_wsl plugin 源码在 allow-list 之外有差异则失败。

allow-list 只放真正的平台入口（默认仅 .mcp.json）。运行时数据/缓存/VCS 不参与
比对。 ALLOWLIST 的每一项都必须有理由；首次运行若报告其它合法平台文件，把它
们连同理由加入 ALLOWLIST 后再跑绿。"""
import hashlib
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WIN = REPO / "v2_win" / "cc-communicate"
WSL = REPO / "v2_wsl" / "cc-communicate"

ALLOWLIST = {".mcp.json"}  # 平台 MCP command（win: python / wsl: python3）
EXCLUDE_DIRS = {"data", "__pycache__", ".git", ".pytest_cache", "node_modules"}
EXCLUDE_SUFFIXES = {".pyc", ".log"}


def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _files(root: Path) -> dict:
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in EXCLUDE_SUFFIXES:
                continue
            full = Path(dirpath) / fn
            out[full.relative_to(root).as_posix()] = full
    return out


def main() -> int:
    win, wsl = _files(WIN), _files(WSL)
    problems = []
    for rel in sorted(set(win) | set(wsl)):
        if rel in ALLOWLIST:
            continue
        if rel not in win:
            problems.append("only in wsl: " + rel)
        elif rel not in wsl:
            problems.append("only in win: " + rel)
        elif _hash(win[rel]) != _hash(wsl[rel]):
            problems.append("differs: " + rel)
    if problems:
        print("PARITY FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("PARITY OK (%d files compared, allowlist=%s)" % (len(win), sorted(ALLOWLIST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

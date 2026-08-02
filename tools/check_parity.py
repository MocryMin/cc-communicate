"""HP-13-B: 若 v2_win 与 v2_wsl plugin 源码在 allow-list 之外有差异则失败。

allow-list 只放真正的平台入口（默认仅 .mcp.json）。运行时数据/缓存/VCS 不参与
比对。 ALLOWLIST 的每一项都必须有理由；首次运行若报告其它合法平台文件，把它
们连同理由加入 ALLOWLIST 后再跑绿。
HP-13-A: with tools/build_artifacts.py owning the generation of v2_wsl, a
parity pass means "committed v2_wsl matches what the generator produces" for
all non-allowlisted files; .mcp.json is pinned to the platform templates by
build_artifacts.py verify (closes the allowlist hole)."""
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


def collect(root: Path) -> dict:
    """All files under root, posix-relpath -> Path, applying EXCLUDE_DIRS /
    EXCLUDE_SUFFIXES. Shared with tools/build_artifacts.py - the generator's
    file-set rules can never drift from the parity gate's."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if os.path.splitext(fn)[1] in EXCLUDE_SUFFIXES:
                continue
            full = Path(dirpath) / fn
            out[full.relative_to(root).as_posix()] = full
    return out


def compare(win: dict, wsl: dict, allowlist=ALLOWLIST):
    """Diff two collect() maps. Returns (problems, compared) where compared is
    the number of non-allowlisted files in the union."""
    compared = [rel for rel in sorted(set(win) | set(wsl)) if rel not in allowlist]
    problems = []
    for rel in compared:
        if rel not in win:
            problems.append("only in wsl: " + rel)
        elif rel not in wsl:
            problems.append("only in win: " + rel)
        elif _hash(win[rel]) != _hash(wsl[rel]):
            problems.append("differs: " + rel)
    return problems, len(compared)


def main() -> int:
    for name, tree in (("win", WIN), ("wsl", WSL)):
        if not tree.is_dir():
            print("PARITY FAIL: %s tree is not an existing directory: %s" % (name, tree))
            return 1
    problems, compared = compare(collect(WIN), collect(WSL))
    if compared == 0:
        # Vacuous-pass guard: covers empty trees AND trees reduced to only
        # allowlisted files (0 meaningful compares must never print OK).
        print("PARITY FAIL: 0 non-allowlisted files to compare - refusing to "
              "pass having compared nothing (trees: %s, %s)" % (WIN, WSL))
        return 1
    if problems:
        print("PARITY FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("PARITY OK (%d files compared, allowlist=%s)" % (compared, sorted(ALLOWLIST)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

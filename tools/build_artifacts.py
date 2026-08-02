"""HP-13-A: canonical v2_win source -> generated v2_wsl artifact (design:
docs/superpowers/specs/2026-08-03-wave4-hp13-source-unification-design.md).

v2_win/cc-communicate/ is the single committed source of truth. This tool
generates v2_wsl/cc-communicate/ from it (byte-for-byte mirror, .mcp.json
substituted from the platform templates in tools/artifact_templates/) and
verifies that the committed artifacts match the generator output.

Usage:
  py -3 tools/build_artifacts.py generate   # rewrite v2_wsl from v2_win
  py -3 tools/build_artifacts.py verify     # gate: committed trees == generated

Workflow rule: edit v2_win/ only -> generate -> commit both trees.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Same file-set rules as the parity gate - never drift.
from check_parity import WIN, WSL, collect

# TEMPLATE_DIR is the monkeypatchable test seam (tests reassign
# build_artifacts.TEMPLATE_DIR), so template paths are resolved from it at
# call time via _win_template()/_wsl_template(); the frozen constants below
# are the pinned default references for the CLI and documentation.
TEMPLATE_DIR = Path(__file__).resolve().parent / "artifact_templates"
WIN_TEMPLATE = TEMPLATE_DIR / "mcp.win.json"
WSL_TEMPLATE = TEMPLATE_DIR / "mcp.wsl.json"


def _win_template() -> Path:
    return TEMPLATE_DIR / "mcp.win.json"


def _wsl_template() -> Path:
    return TEMPLATE_DIR / "mcp.wsl.json"


def build_wsl_tree(win_root: Path, dst: Path) -> None:
    """Mirror win_root into dst (both are cc-communicate/ plugin roots).

    Every file collected by the parity walk is copied byte-for-byte except
    .mcp.json, which is written from the WSL template. data/ is never
    enumerated (it is excluded by collect()) so it is never touched."""
    for rel, src in collect(win_root).items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel == ".mcp.json":
            target.write_bytes(_wsl_template().read_bytes())
        else:
            target.write_bytes(src.read_bytes())


def generate() -> int:
    build_wsl_tree(WIN, WSL)
    # Mirror semantics: files present in v2_wsl but absent from v2_win are
    # stale (deletions must propagate). .mcp.json is always in win, so a
    # stale removal implies a real deletion.
    win_rels = set(collect(WIN))
    for rel in collect(WSL):
        if rel not in win_rels:
            (WSL / rel).unlink()
    print("GENERATED v2_wsl/cc-communicate (%d files)" % len(win_rels))
    return 0


def verify() -> int:
    problems = []
    # 1. The canonical tree's own .mcp.json must be the win template (closes
    #    the parity allowlist hole - .mcp.json legitimately differs per tree).
    win_mcp = WIN / ".mcp.json"
    if not win_mcp.is_file():
        problems.append("missing v2_win/cc-communicate/.mcp.json")
    elif win_mcp.read_bytes() != _win_template().read_bytes():
        problems.append("v2_win/cc-communicate/.mcp.json != mcp.win.json")
    # 2. Regenerate the expected WSL tree into a temp dir (pure computation;
    #    zero writes to the repo) and byte-compare with the committed tree.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "wsl"
        build_wsl_tree(WIN, tmp)
        committed, generated = collect(WSL), collect(tmp)
        compared = 0
        for rel in sorted(set(committed) | set(generated)):
            compared += 1
            if rel not in generated:
                problems.append("stale in committed v2_wsl: " + rel)
            elif rel not in committed:
                problems.append("missing in committed v2_wsl: " + rel)
            elif committed[rel].read_bytes() != generated[rel].read_bytes():
                problems.append("differs: " + rel)
        if compared == 0:
            problems.append("0 files compared - refusing to pass")
    if problems:
        print("ARTIFACTS FAIL:")
        for p in problems:
            print("  " + p)
        return 1
    print("ARTIFACTS OK (%d files compared, templates pinned)" % compared)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("generate", "verify"))
    args = ap.parse_args(argv)
    return generate() if args.command == "generate" else verify()


if __name__ == "__main__":
    sys.exit(main())

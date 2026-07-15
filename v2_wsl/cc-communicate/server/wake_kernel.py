"""Remote-wake entrypoint (v2.2 Amd8 / BUG-4).

Run (locally or via cross-machine exec) to ensure the local kernel is alive.
`call_remote` invokes this on the remote machine when its kernel appears dead,
so a cross-machine RPC doesn't silently fail when no remote CC is active.

Reuses ensure_core()'s filelock mutex -> single instance preserved even under
concurrent wakes from both machines. Exit 0 if the kernel is alive after the
call, 1 if it could not be started."""
from __future__ import annotations

import sys

from check_core import ensure_core


def main():
    ok = ensure_core()
    print("alive" if ok else "failed")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

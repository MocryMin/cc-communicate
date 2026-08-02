"""cc-communicate data-root migration/validation tool (HP-11, D2).

Validates a data root's layout + schema_version conventions and migrates the
v1 registries to the versioned convention (wrap the flat registries, stamp
machine_identity). REFUSES (exit 1, file untouched) when any state file's
schema_version is NEWER than the supported one - an older plugin must never
silently misread a newer data root.

Usage:
  py -3 tools/migrate_data.py --data-root <dir> [--dry-run]

Run with the kernel stopped. Idempotent: a second run is a no-op.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "v2_win", "cc-communicate", "server")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", required=True,
                    help="data root to validate/migrate")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only; write nothing")
    args = ap.parse_args(argv)

    # The server modules bind their paths at import from
    # CC_COMMUNICATE_DATA_DIR - set it BEFORE importing them.
    os.environ["CC_COMMUNICATE_DATA_DIR"] = os.path.abspath(args.data_root)
    if SERVER not in sys.path:
        sys.path.insert(0, SERVER)
    import schema

    errors, warnings = schema.validate_layout(args.data_root)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    migrated = 0
    for rel, key in schema.WRAP_TARGETS:
        path = os.path.join(args.data_root, rel)
        if not os.path.isfile(path):
            continue
        if args.dry_run:
            if schema.needs_wrap(path, key):
                print(f"WOULD {rel} -> wrap schema_version 1")
                migrated += 1
        elif schema.wrap_v1(path, key):
            print(f"WRAP  {rel} -> schema_version 1")
            migrated += 1
    for rel in schema.STAMP_TARGETS:
        path = os.path.join(args.data_root, rel)
        if not os.path.isfile(path):
            continue
        if args.dry_run:
            if schema.needs_stamp(path):
                print(f"WOULD {rel} -> stamp schema_version 1")
                migrated += 1
        elif schema.stamp_v1(path):
            print(f"STAMP {rel} -> schema_version 1")
            migrated += 1
    if args.dry_run:
        print(f"dry-run: {migrated} file(s) would be migrated; nothing written")
    else:
        print(f"migrated {migrated} file(s)")
    print("REFUSED (newer schema files present)" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

"""T32: headless-started WSL kernel (no claude ancestor) must still find a
native Linux claude for spawns - NOT the interop Windows claude.exe (C13).

Tests run on the Windows host; the Linux branch is exercised by monkeypatching
os.name and the FS helpers. machine_identity is reloaded by the conftest
fixture, so monkeypatching the module object is per-test-safe.

Monkeypatch targets adapted to the real import shape (brief allowed this):
- claude_binary_path is imported FUNCTION-LOCALLY inside _detect_claude_bin
  (`from proc import ...` - not a module attribute of machine_identity), so we
  patch server.proc.claude_binary_path (the proc module attribute, also
  reloaded by the fixture).
- shutil is imported lazily inside _native_linux_claude (not a module
  attribute either), so we patch the global shutil.which.
- the code calls os.access() - os.path (ntpath/posixpath) has NO `access`
  attribute, so we patch os.access, not os.path.access.
- tmp_path is Windows-flavored while the candidates use "/" and "~": the
  expanduser fake normalizes via os.path.normpath and the fixture files use
  the DOTTED dir names (.npm-global / .local) that match the real candidates,
  so string equality holds on the host.
"""
import json
import os
import shutil

import pytest


def test_native_linux_claude_finds_static_candidate(server, monkeypatch, tmp_path):
    """Ancestor walk finds nothing; the static ~/.npm-global candidate exists
    and is executable -> picked."""
    mi = server.machine_identity
    native = tmp_path / ".npm-global" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.write_text("#!/bin/sh\n")

    monkeypatch.setattr(server.proc, "claude_binary_path", lambda pid: None)
    monkeypatch.setattr(mi.os.path, "expanduser",
                        lambda p: os.path.normpath(p.replace("~", str(tmp_path))))
    monkeypatch.setattr(mi.os.path, "isfile",
                        lambda p: p == str(native))
    monkeypatch.setattr(os, "access",
                        lambda p, mode: p == str(native))
    monkeypatch.setattr(shutil, "which", lambda name: None)  # no npm on PATH
    monkeypatch.setattr(os, "name", "posix")

    assert mi._detect_claude_bin() == str(native)


def test_native_linux_claude_rejects_interop_npm_path(server, monkeypatch, tmp_path):
    """npm resolves to an interop /mnt/c/... path -> its derived candidate is
    rejected; a static candidate still wins."""
    mi = server.machine_identity
    native = tmp_path / ".local" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.write_text("#!/bin/sh\n")

    monkeypatch.setattr(server.proc, "claude_binary_path", lambda pid: None)
    interop_npm = "/mnt/c/Users/u/AppData/Roaming/npm"
    monkeypatch.setattr(shutil, "which", lambda name: interop_npm)
    monkeypatch.setattr(mi.os.path, "expanduser",
                        lambda p: os.path.normpath(p.replace("~", str(tmp_path))))
    monkeypatch.setattr(mi.os.path, "isfile",
                        lambda p: p == str(native))
    monkeypatch.setattr(os, "access",
                        lambda p, mode: p == str(native))
    monkeypatch.setattr(os, "name", "posix")

    assert mi._detect_claude_bin() == str(native)


def test_load_or_create_redetects_null_claude_bin(server, monkeypatch, tmp_path):
    """An existing identity with claude_bin: null (persisted by an old
    headless kernel) is re-detected and rewritten - the T32 upgrade."""
    mi = server.machine_identity
    ident_file = tmp_path / "machine_identity.json"
    ident_file.write_text(json.dumps(
        {"type": "wsl-ubuntu", "id": "wsl-id-1", "claude_bin": None}))
    monkeypatch.setattr(mi, "MACHINE_IDENTITY_FILE", str(ident_file))
    monkeypatch.setattr(mi, "detect_type", lambda: "wsl-ubuntu")
    monkeypatch.setattr(mi, "_detect_claude_bin",
                        lambda: "/home/u/.npm-global/bin/claude")
    monkeypatch.setattr(os, "name", "posix")

    ident = mi.load_or_create()
    assert ident["claude_bin"] == "/home/u/.npm-global/bin/claude"
    with open(ident_file, encoding="utf-8") as f:
        assert json.load(f)["claude_bin"] == "/home/u/.npm-global/bin/claude"


def test_load_or_create_keeps_valid_claude_bin(server, monkeypatch, tmp_path):
    """A non-null claude_bin is NOT re-detected (stability)."""
    mi = server.machine_identity
    ident_file = tmp_path / "machine_identity.json"
    ident_file.write_text(json.dumps(
        {"type": "wsl-ubuntu", "id": "wsl-id-1",
         "claude_bin": "/home/u/.npm-global/bin/claude"}))
    monkeypatch.setattr(mi, "MACHINE_IDENTITY_FILE", str(ident_file))
    monkeypatch.setattr(mi, "detect_type", lambda: "wsl-ubuntu")
    monkeypatch.setattr(mi, "_detect_claude_bin", lambda: "SHOULD-NOT-HAPPEN")
    monkeypatch.setattr(os, "name", "posix")

    ident = mi.load_or_create()
    assert ident["claude_bin"] == "/home/u/.npm-global/bin/claude"

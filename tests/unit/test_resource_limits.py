"""HP-09: RESOURCE_EXHAUSTED activation (inline cap) - entry envelope + validator."""
import pytest
from result import Code

import mcp_server


def test_over_limit_entry_is_resource_exhausted(server):
    big = "x" * (server.validation.MAX_INLINE_BYTES + 1)
    r = mcp_server.send_message("a", "b", big)
    assert r["ok"] is False
    assert r["code"] == Code.RESOURCE_EXHAUSTED
    assert r["retryable"] is False
    assert r["data"] == {"limit_bytes": server.validation.MAX_INLINE_BYTES,
                         "actual_bytes": len(big.encode("utf-8"))}


def test_validate_message_size_raises_resource_exhausted(server):
    v = server.validation
    with pytest.raises(v.ResourceExhaustedError) as ei:
        v.validate_message_size("x" * (v.MAX_INLINE_BYTES + 1))
    assert ei.value.code == Code.RESOURCE_EXHAUSTED
    assert ei.value.data == {"limit_bytes": v.MAX_INLINE_BYTES,
                             "actual_bytes": v.MAX_INLINE_BYTES + 1}


def test_validate_message_size_inline_ok(server):
    v = server.validation
    assert v.validate_message_size("hi" * 100) == "hi" * 100


def test_validate_message_size_non_str_still_invalid(server):
    v = server.validation
    with pytest.raises(v.InvalidArgumentError):
        v.validate_message_size(42)

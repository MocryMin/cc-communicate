"""HP-07: envelope v2 - uniform 5-field shape, NOT_ALIVE code, retryable."""
import pytest

from result import Code, ok, err


def test_ok_shape(server):
    r = ok({"a": 1})
    assert r == {"ok": True, "code": None, "message": None,
                 "data": {"a": 1}, "retryable": False}


def test_ok_none_data(server):
    assert ok()["data"] is None and ok()["ok"] is True


def test_err_shape(server):
    r = err(Code.TIMEOUT, "no reply", data={"conn": "x"}, retryable=True)
    assert r == {"ok": False, "code": Code.TIMEOUT, "message": "no reply",
                 "data": {"conn": "x"}, "retryable": True}


def test_err_defaults(server):
    r = err(Code.NOT_FOUND, "gone")
    assert r["code"] == Code.NOT_FOUND and r["data"] is None
    assert r["retryable"] is False


def test_not_alive_code(server):
    assert Code.NOT_ALIVE == "NOT_ALIVE"

"""HP-09: artifact_refs schema + record payload (delivery in Task 3)."""
import json
import os

import pytest
from result import Code

import mcp_server


def _conv_pair(server):
    server.paths.ensure_runtime_dirs()
    k = server.kernel
    k.alive_conversations[("a", "b")] = {"established_at": 1.0}
    return k


def _pipe_records(server):
    d = server.conversations.conv_dir("a", "b")
    pipe = os.path.join(d, "pipe")
    out = []
    for fname in os.listdir(pipe):
        with open(os.path.join(pipe, fname), encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def test_validate_artifact_refs_matrix(server):
    v = server.validation
    assert v.validate_artifact_refs(None) == []
    ok = v.validate_artifact_refs([{"path": "/tmp/x", "size": 10,
                                    "sha256": "a" * 64,
                                    "media_type": "text/plain"}])
    assert ok == [{"path": "/tmp/x", "size": 10, "sha256": "a" * 64,
                   "media_type": "text/plain"}]
    ok_uri = v.validate_artifact_refs([{"uri": "file:///tmp/x", "size": 1,
                                        "sha256": "b" * 64,
                                        "media_type": "x/y"}])
    assert ok_uri[0]["uri"] == "file:///tmp/x"
    bads = [
        [{"size": 10, "sha256": "a" * 64, "media_type": "t"}],           # neither
        [{"path": "/x", "uri": "file:///x", "size": 10, "sha256": "a" * 64,
          "media_type": "t"}],                                           # both
        [{"path": "", "size": 10, "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": -1, "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": "10", "sha256": "a" * 64, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "A" * 64, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "a" * 63, "media_type": "t"}],
        [{"path": "/x", "size": 10, "sha256": "a" * 64, "media_type": ""}],
        ["not-a-dict"],
    ]
    for refs in bads:
        with pytest.raises(v.InvalidArgumentError):
            v.validate_artifact_refs(refs)


def test_validate_artifact_refs_cap(server):
    v = server.validation
    v.MAX_ARTIFACT_REFS = 2
    refs = [{"path": f"/x{i}", "size": 1, "sha256": "a" * 64,
             "media_type": "t"} for i in range(3)]
    with pytest.raises(v.InvalidArgumentError):
        v.validate_artifact_refs(refs)


def test_send_with_refs_stores_payload(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"path": "/tmp/build.log", "size": 2048, "sha256": "c" * 64,
             "media_type": "text/plain"}]
    r = ka.send_message(server.kernel.alive_conversations, {}, "store",
                        "a", "b", "build attached", artifact_refs=refs)
    assert r["sent"] is True
    recs = _pipe_records(server)
    assert len(recs) == 1
    assert recs[0]["payload"]["text"] == "build attached"
    assert recs[0]["payload"]["artifact_refs"] == refs


def test_send_without_refs_payload_unchanged(server):
    ka = server.kernel_api
    _conv_pair(server)
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "plain")
    rec = _pipe_records(server)[0]
    assert "artifact_refs" not in rec["payload"]


def test_over_limit_text_with_refs_still_rejected(server):
    v = server.validation
    big = "x" * (v.MAX_INLINE_BYTES + 1)
    r = mcp_server.send_message("a", "b", big, artifact_refs=[
        {"path": "/tmp/x", "size": 1, "sha256": "a" * 64, "media_type": "t"}])
    assert r["ok"] is False and r["code"] == Code.RESOURCE_EXHAUSTED


def test_bad_refs_at_entry_rejected(server):
    r = mcp_server.send_message("a", "b", "hi", artifact_refs=[
        {"path": "/x", "size": -1, "sha256": "a" * 64, "media_type": "t"}])
    assert r["ok"] is False and r["code"] == Code.INVALID_ARGUMENT


def test_listen_v2_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"uri": "file:///x", "size": 5, "sha256": "d" * 64,
             "media_type": "text/plain"}]
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "hi", artifact_refs=refs)
    res = ka.listen_scan_v2({}, "store", "b", 0)
    assert len(res["messages"]) == 1
    assert res["messages"][0]["payload"]["artifact_refs"] == refs


def test_legacy_listen_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    seq = {}
    refs = [{"path": "/tmp/x", "size": 1, "sha256": "e" * 64,
             "media_type": "t"}]
    r1 = ka.send_message(server.kernel.alive_conversations, seq, "store",
                         "a", "b", "hi", artifact_refs=refs)
    res = ka.listen_scan({}, "b", 0)
    assert res["messages"][0]["artifact_refs"] == refs
    # ack the first message (archive) so the next listen returns only the
    # new one - otherwise both sit in the pipe and listdir order decides
    ka.listen_scan({}, "b", r1["ts"])
    # ref-less records carry NO artifact_refs key (zero-change for old readers)
    ka.send_message(server.kernel.alive_conversations, seq, "store",
                    "a", "b", "plain")
    res2 = ka.listen_scan({}, "b", 0)
    assert len(res2["messages"]) == 1
    assert "artifact_refs" not in res2["messages"][0]


def test_collect_messages_delivers_refs(server):
    ka = server.kernel_api
    _conv_pair(server)
    refs = [{"path": "/tmp/x", "size": 1, "sha256": "f" * 64,
             "media_type": "t"}]
    ka.send_message(server.kernel.alive_conversations, {}, "store",
                    "a", "b", "hi", artifact_refs=refs)
    msgs = ka.collect_messages("b")
    assert msgs[0]["artifact_refs"] == refs

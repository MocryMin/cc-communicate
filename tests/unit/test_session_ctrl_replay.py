import json
import os


def test_session_ctrl_start_replay(server):
    k = server.kernel
    # 与真实 kernel.main() 一致：process_session_ctrl_event 末尾会 _save_sessions()，
    # 需要 SERVER_DATA_DIR 存在（ensure_runtime_dirs 也会建 SESSION_CTRL_DIR）。
    server.paths.ensure_runtime_dirs()
    ev_dir = server.paths.SESSION_CTRL_DIR
    os.makedirs(ev_dir, exist_ok=True)
    sid = "sess-xyz"
    # start_time 用真实生产者（registrar.js/proc.js）写域内的值：ISO/WMI 字符串
    # 或 null。float epoch 不在该域内（parse_start_time 只对 str 做 .strip()）。
    event = {
        "event": "start", "event_ts": 1000, "session_id": sid, "pid": 999,
        "cwd": "/tmp/x", "start_time": "2026-07-24T10:00:00", "source": None,
    }
    with open(os.path.join(ev_dir, "start_1000_%s.json" % sid), "w", encoding="utf-8") as f:
        json.dump(event, f)

    k.process_session_ctrl_event()
    assert sid in k.sessions
    assert k.sessions[sid]["pid"] == 999

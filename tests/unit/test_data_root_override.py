import importlib
import os


def test_data_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_COMMUNICATE_DATA_DIR", str(tmp_path))
    import paths
    importlib.reload(paths)
    assert paths.DATA_DIR == os.path.abspath(str(tmp_path))
    assert paths.SESSION_CTRL_DIR == os.path.join(paths.DATA_DIR, "session_ctrl")
    assert paths.CONVERSATIONS_DIR == os.path.join(paths.DATA_DIR, "conversations")


def test_data_dir_default_when_unset(monkeypatch):
    monkeypatch.delenv("CC_COMMUNICATE_DATA_DIR", raising=False)
    import paths
    importlib.reload(paths)
    assert paths.DATA_DIR == os.path.join(paths.PLUGIN_ROOT, "data")

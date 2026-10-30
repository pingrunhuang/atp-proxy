import importlib.util
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("login_diagnostic_entry", ROOT / "scripts/test_login.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import _login_check
    return module, _login_check


@pytest.mark.parametrize("md,td,enabled,expected", [
    (True, True, True, 0), (False, True, True, 1),
    (True, False, True, 1), (False, False, True, 1),
    (False, True, False, 0),
])
def test_reports_individual_login_results(diagnostic, capsys, md, td, enabled, expected):
    _, runner = diagnostic
    session = SimpleNamespace(md_ready=threading.Event(), td_ready=threading.Event())
    def connect(timeout):
        if md:
            session.md_ready.set()
        if td:
            session.td_ready.set()
        return td and (md or not enabled)
    session.connect = connect
    assert runner.check_session(session, 0.2, enabled) == expected
    output = capsys.readouterr().out
    assert f"TD: {'PASS' if td else 'FAIL'}" in output
    assert f"MD: {'SKIP' if not enabled else ('PASS' if md else 'FAIL')}" in output


def test_native_connect_hang_has_bounded_wait(diagnostic, capsys):
    _, runner = diagnostic
    release = threading.Event()
    session = SimpleNamespace(md_ready=threading.Event(), td_ready=threading.Event(),
                              connect=lambda timeout: release.wait(1))
    try:
        assert runner.check_session(session, 0.01, True) == 1
        assert "FAIL / timeout" in capsys.readouterr().out
    finally:
        release.set()


def test_native_connect_exception_is_reported(diagnostic):
    _, runner = diagnostic
    def connect(timeout):
        raise RuntimeError("SDK unavailable")
    session = SimpleNamespace(md_ready=threading.Event(), td_ready=threading.Event(), connect=connect)
    with pytest.raises(RuntimeError, match="SDK unavailable"):
        runner.check_session(session, 0.2, True)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_rejects_unbounded_timeout(diagnostic, value):
    module, _ = diagnostic
    with pytest.raises(SystemExit) as exc:
        module.main(["--timeout", value])
    assert exc.value.code == 2


def test_parent_kills_stuck_worker(diagnostic, monkeypatch):
    _, runner = diagnostic
    class Child:
        killed = False
        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("worker", timeout)
            return -9
        def poll(self):
            return None if not self.killed else -9
        def kill(self):
            self.killed = True
    child = Child()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: child)
    assert runner.run_cli(ROOT / "scripts/test_login.py", runner.parser_for("test"), None, []) == 1
    assert child.killed


def test_worker_loads_env_without_overriding_process(diagnostic, monkeypatch, tmp_path):
    _, runner = diagnostic
    env = tmp_path / "check.env"
    env.write_text("LOGIN_DIAGNOSTIC_VALUE=file\nLOGIN_DIAGNOSTIC_SECOND=loaded\n")
    monkeypatch.setenv("LOGIN_DIAGNOSTIC_VALUE", "process")
    monkeypatch.delenv("LOGIN_DIAGNOSTIC_SECOND", raising=False)
    import os
    def worker(args):
        assert os.environ["LOGIN_DIAGNOSTIC_VALUE"] == "process"
        assert os.environ["LOGIN_DIAGNOSTIC_SECOND"] == "loaded"
        return 0
    try:
        assert runner.run_cli(__file__, runner.parser_for("test"), worker,
                              ["--worker", "--env-file", str(env)]) == 0
    finally:
        os.environ.pop("LOGIN_DIAGNOSTIC_SECOND", None)


@pytest.mark.parametrize("md", [True, False])
def test_entry_uses_isolated_native_session_and_closes(diagnostic, monkeypatch, tmp_path, md):
    import sys
    from unittest.mock import Mock
    module, _ = diagnostic
    settings = SimpleNamespace(front_td="td", front_md="md", td_host="td", td_port=1,
                               md_host="md", md_port=2, validate=Mock())
    session = SimpleNamespace(close=Mock())
    factory = Mock(return_value=session)
    registry = SimpleNamespace(close=Mock())
    monkeypatch.setitem(sys.modules, "config", SimpleNamespace(Settings=SimpleNamespace(from_env=lambda: settings)))
    monkeypatch.setitem(sys.modules, "atp_session", SimpleNamespace(AtpSession=factory))
    monkeypatch.setitem(sys.modules, "main", SimpleNamespace(configure_native_locale=lambda: None))
    monkeypatch.setitem(sys.modules, "registry", SimpleNamespace(RhOrderRegistry=lambda path: registry))
    monkeypatch.chdir(tmp_path)
    checker = Mock(return_value=0)
    monkeypatch.setattr(module, "check_session", checker)
    assert module.run_check(SimpleNamespace(md=md, timeout=1)) == 0
    assert settings.enable_md is md
    assert settings.initial_symbols == []
    assert getattr(settings, "flow_path") == tmp_path / "flow"
    session.close.assert_called_once()
    checker.assert_called_once_with(session, 1, md, tap=False)
    settings.validate.assert_called_once_with()
    if "atp" == "tap":
        assert settings.reconnect_max_attempts == 0
    if "atp" == "rh":
        registry.close.assert_called_once()
    checker.side_effect = RuntimeError("login failed")
    with pytest.raises(RuntimeError, match="login failed"):
        module.run_check(SimpleNamespace(md=md, timeout=1))
    assert session.close.call_count == 2

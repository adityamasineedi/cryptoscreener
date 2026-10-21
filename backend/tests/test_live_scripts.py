"""P12 / D128a: the pinned, no-reload live scripts (scripts/live/*.ps1).

Argument checks run the real scripts with -DryRun or bad input; nothing is created. The end-to-end test runs a
dummy FastAPI app (no database, no Redis, no exchange) from a throw-away git repository on a free localhost port,
so it never sees the live checkout, the live port or a live process: every script gets that temp repo as
-LiveRoot and a -ProcessMatch that only matches the dummy server's own command line.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or shutil.which("powershell") is None, reason="Windows PowerShell scripts"
)

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "live"
NO_MATCH = "d128-test-matches-no-process"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ps(script: str, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPTS / script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / "test.env"
    path.write_text("# empty: the scripts never read it\n", encoding="utf-8")
    return path


def _common(tmp_path: Path, env_file: Path, port: int, live_root: Path = REPO) -> list[str]:
    return ["-LiveRoot", str(live_root), "-DeployDir", str(tmp_path / "deploy"), "-EnvFile", str(env_file),
            "-Python", sys.executable, "-Port", str(port), "-ProcessMatch", NO_MATCH]


# --- argument checks ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc;whoami", "-rf", "HEAD..main", "a b", "$(calc)"])
def test_start_rejects_commit_syntax(tmp_path, env_file, bad):
    r = _ps("start_live.ps1", "-Commit", bad, "-DryRun", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "commit" in r.stderr.lower()


def test_start_rejects_unknown_commit(tmp_path, env_file):
    r = _ps("start_live.ps1", "-Commit", "deadbeefdeadbeef", "-DryRun", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 2
    assert "is not a commit" in r.stderr


def test_start_rejects_deploy_dir_inside_live_root(tmp_path, env_file):
    args = _common(tmp_path, env_file, _free_port())
    args[args.index("-DeployDir") + 1] = str(REPO / "deploy-inside")
    r = _ps("start_live.ps1", "-Commit", "HEAD", "-DryRun", *args)
    assert r.returncode == 2
    assert "outside -LiveRoot" in r.stderr
    assert not (REPO / "deploy-inside").exists()


@pytest.mark.parametrize("port", ["0", "70000"])
def test_start_rejects_bad_port(tmp_path, env_file, port):
    args = _common(tmp_path, env_file, 8000)
    args[args.index("-Port") + 1] = port
    r = _ps("start_live.ps1", "-Commit", "HEAD", "-DryRun", *args)
    assert r.returncode == 2
    assert "-Port" in r.stderr


def test_start_rejects_missing_env_file_and_python(tmp_path, env_file):
    args = _common(tmp_path, env_file, _free_port())
    args[args.index("-EnvFile") + 1] = str(tmp_path / "missing.env")
    r = _ps("start_live.ps1", "-Commit", "HEAD", "-DryRun", *args)
    assert r.returncode == 2 and "-EnvFile" in r.stderr
    args = _common(tmp_path, env_file, _free_port())
    args[args.index("-Python") + 1] = str(tmp_path / "python.exe")
    r = _ps("start_live.ps1", "-Commit", "HEAD", "-DryRun", *args)
    assert r.returncode == 2 and "-Python" in r.stderr


def test_first_start_needs_commit(tmp_path, env_file):
    r = _ps("start_live.ps1", "-DryRun", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 2
    assert "needs -Commit" in r.stderr


def test_auto_takes_no_commit(tmp_path, env_file):
    r = _ps("start_live.ps1", "-Auto", "-Commit", "HEAD", "-DryRun", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 2


def test_dry_run_plans_without_reload_and_changes_nothing(tmp_path, env_file):
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q")
    head = _commit_dummy(live, "v1")
    r = _ps("start_live.ps1", "-Commit", head, "-DryRun", *_common(tmp_path, env_file, _free_port(), live_root=live))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DRY RUN" in r.stdout
    assert "-m uvicorn app.main:app" in r.stdout and "--env-file" in r.stdout and "--app-dir" in r.stdout
    assert "--reload" not in r.stdout
    assert "worktree add --detach" in r.stdout and head in r.stdout
    assert not (tmp_path / "deploy").exists()


def test_deploy_requires_commit(tmp_path, env_file):
    r = _ps("deploy_live.ps1", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 2
    assert "-Commit" in r.stderr


def test_deploy_without_deploy_worktree_is_refused(tmp_path, env_file):
    r = _ps("deploy_live.ps1", "-Commit", "HEAD", "-DryRun", *_common(tmp_path, env_file, _free_port()))
    assert r.returncode == 3
    assert "start_live.ps1 -Commit" in r.stderr


def test_rollback_without_history_is_refused(tmp_path, env_file):
    r = _ps("rollback_live.ps1", "-DryRun", "-LiveRoot", str(REPO), "-DeployDir", str(tmp_path / "deploy"), "-ProcessMatch", NO_MATCH)
    assert r.returncode == 3


def test_health_check_fails_when_nothing_runs(tmp_path):
    r = _ps("health_check.ps1", "-Port", str(_free_port()), "-LiveRoot", str(tmp_path), "-DeployDir",
            str(tmp_path / "deploy"), "-ProcessMatch", NO_MATCH, "-TimeoutSeconds", "2", "-Json")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["verdict"] == "FAIL"
    assert {c["check"] for c in out["checks"] if c["status"] == "FAIL"} >= {"api_health", "writer_lock", "one_listener"}


def test_register_autostart_dry_run(tmp_path):
    r = _ps("register_autostart.ps1", "-DryRun", "-DeployDir", str(tmp_path / "deploy"))
    assert r.returncode == 0
    assert "-Auto" in r.stdout and "every 5 min" in r.stdout and "Interactive" in r.stdout


def test_uvicorn_args_never_include_reload():
    common = (SCRIPTS / "_common.ps1").read_text(encoding="utf-8")
    body = common[common.index("function Get-UvicornArgs"):]
    body = body[: body.index("\n}")]
    body = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))
    assert "--reload" not in body and "'app.main:app'" in body
    for name in ("start_live.ps1", "deploy_live.ps1", "rollback_live.ps1"):
        text = re.sub(r"<#.*?#>", "", (SCRIPTS / name).read_text(encoding="utf-8"), flags=re.S)
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        assert "--reload" not in code, name


# --- end to end on a dummy app -------------------------------------------------------------------------------

DUMMY_APP = '''
from fastapi import FastAPI

app = FastAPI()
VERSION = "{version}"


@app.get("/api/health")
def health():
    return {{"status": "ok", "database": "ok", "version": VERSION}}


@app.get("/api/paper/status")
def paper_status():
    return {{"writer_lock_held": True, "ledger_ready": True, "version": VERSION}}
'''


def _commit_dummy(repo: Path, version: str) -> str:
    (repo / "backend" / "app").mkdir(parents=True, exist_ok=True)
    (repo / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "backend" / "app" / "main.py").write_text(DUMMY_APP.format(version=version), encoding="utf-8")
    (repo / "data" / "paper").mkdir(parents=True, exist_ok=True)
    (repo / "backend" / "data").mkdir(parents=True, exist_ok=True)
    (repo / ".gitignore").write_text("data/\nbackend/data/\n__pycache__/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", version)
    return _git(repo, "rev-parse", "HEAD")


def _kill_test_servers(port: int) -> None:
    """Last resort: stop any process whose command line is this test's dummy server (its own port only)."""
    ps = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app\\.main:app .*--port %d ' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" % port
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=60)


def _version(port: int) -> str | None:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as resp:
            return json.loads(resp.read())["version"]
    except OSError:
        return None


def test_start_health_deploy_rollback_stop_end_to_end(tmp_path, env_file):
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("fastapi/uvicorn not installed")
    live = tmp_path / "live"
    live.mkdir()
    _git(live, "init", "-q")
    c1 = _commit_dummy(live, "v1")
    c2 = _commit_dummy(live, "v2")
    port = _free_port()
    deploy = tmp_path / "deploy"
    match = f"app\\.main:app .*--port {port} "
    common = ["-LiveRoot", str(live), "-DeployDir", str(deploy), "-EnvFile", str(env_file), "-Python", sys.executable,
              "-Port", str(port), "-ProcessMatch", match]
    logs = ["-LogDir", str(tmp_path / "logs")]
    try:
        r = _ps("start_live.ps1", "-Commit", c1, "-BindHost", "127.0.0.1", "-WaitSeconds", "60", *common, *logs)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _version(port) == "v1"
        # State links: the deploy worktree writes into the live root's data folder.
        assert (deploy / "data" / "paper").is_dir()
        (deploy / "data" / "paper" / "probe.txt").write_text("x", encoding="utf-8")
        assert (live / "data" / "paper" / "probe.txt").exists()
        # Editing the live checkout does not reach the running code (no --reload, pinned worktree).
        (live / "backend" / "app" / "main.py").write_text(DUMMY_APP.format(version="edited"), encoding="utf-8")
        time.sleep(2)
        assert _version(port) == "v1"
        _git(live, "checkout", "--", "backend/app/main.py")

        # A second copy is refused.
        r = _ps("start_live.ps1", *common, *logs)
        assert r.returncode == 3, r.stdout + r.stderr

        # Auto mode does nothing while live is up.
        r = _ps("start_live.ps1", "-Auto", *common, *logs)
        assert r.returncode == 0 and "already running" in r.stdout

        hc = _ps("health_check.ps1", "-Port", str(port), "-LiveRoot", str(live), "-DeployDir", str(deploy),
                 "-ProcessMatch", match, "-Json")
        checks = {c["check"]: c["status"] for c in json.loads(hc.stdout)["checks"]}
        assert hc.returncode == 0, hc.stdout
        assert checks["pinned"] == "OK" and checks["no_reload"] == "OK" and checks["one_backend"] == "OK"

        # Deploy v2 (fast-forward), then roll back to v1.
        dep = ["-LiveRoot", str(live), "-DeployDir", str(deploy), "-EnvFile", str(env_file), "-Python", sys.executable,
               "-Port", str(port), "-BindHost", "127.0.0.1", "-ProcessMatch", match, "-WaitSeconds", "60"]
        r = _ps("deploy_live.ps1", "-Commit", c2, *dep, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _git(deploy, "rev-parse", "HEAD") == c2
        assert _version(port) == "v2"
        r = _ps("deploy_live.ps1", "-Commit", c1, "-DryRun", *dep)
        assert r.returncode == 3 and "not a fast-forward" in r.stderr
        r = _ps("rollback_live.ps1", *dep, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        assert _git(deploy, "rev-parse", "HEAD") == c1
        assert _version(port) == "v1"
        events = [json.loads(line)["event"] for line in (live / "data" / "live_runs.jsonl").read_text().splitlines()]
        assert "deploy" in events and "rollback" in events
    finally:
        try:
            r = _ps("stop_live.ps1", "-Port", str(port), "-LiveRoot", str(live), "-ProcessMatch", match,
                    "-TimeoutSeconds", "30", timeout=120)
        finally:
            _kill_test_servers(port)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "forced stop" not in r.stdout, r.stdout  # CTRL+BREAK shut it down cleanly
    assert _version(port) is None
    assert (live / "data" / "live_stopped_by_owner").exists()
    # Auto-start respects the owner's stop.
    r = _ps("start_live.ps1", "-Auto", *common, *logs)
    assert r.returncode == 0 and "stopped by the owner" in r.stdout
    assert _version(port) is None

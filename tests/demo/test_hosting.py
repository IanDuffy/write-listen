"""Part E, hosting (DEPLOY.md, deploy/): the server's address flags, `wl deploy`, and the droplet's files.
Nothing here touches the network or runs setup.sh; the shell scripts are syntax-checked, and backup.sh
runs against a stand-in rsync."""

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import cli
import server

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPTS = ["setup.sh", "deploy.sh", "backup.sh", "check.sh", "daily-log.sh", "wl-droplet"]
HASH = "$2a$14$" + "abcdefghijklmnopqrstuv" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ01234"  # bcrypt's shape: 22 + 31


# --- server.py --host/--port ------------------------------------------------------------------------

def test_address_defaults_to_loopback():
    assert server.serve_address([], {}) == ("127.0.0.1", 8765)


def test_address_from_the_environment():
    assert server.serve_address([], {"WL_HOST": "0.0.0.0", "WL_PORT": "9000"}) == ("0.0.0.0", 9000)


def test_flags_win_over_the_environment():
    env = {"WL_HOST": "0.0.0.0", "WL_PORT": "9000"}
    assert server.serve_address(["--host", "127.0.0.1", "--port", "8765"], env) == ("127.0.0.1", 8765)
    assert server.serve_address(["--port", "8001"], env) == ("0.0.0.0", 8001)


def test_a_bad_port_stops():
    with pytest.raises(SystemExit):
        server.serve_address(["--port", "eighty"], {})


def test_main_uses_the_flags_and_trusts_only_caddys_forwarded_headers():
    main = (ROOT / "server.py").read_text(encoding="utf-8").split('if __name__ == "__main__":', 1)[1]
    assert "serve_address()" in main
    assert "host=host, port=port" in main
    assert 'forwarded_allow_ips="127.0.0.1"' in main


# --- wl deploy ----------------------------------------------------------------------------------------

class Execd(Exception):
    pass


def test_wl_deploy_execs_the_script(monkeypatch):
    calls = []

    def execvp(file, args):  # exec never returns; nor does this
        calls.append((file, args))
        raise Execd

    monkeypatch.setattr(os, "execvp", execvp)
    with pytest.raises(Execd):
        cli.main(["deploy", "extra"])
    assert calls == [("bash", ["bash", str(ROOT / "deploy" / "deploy.sh"), "extra"])]


# --- the files on the droplet ------------------------------------------------------------------------

@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_parse_and_are_executable(name):
    path = DEPLOY / name
    assert os.access(path, os.X_OK), f"{name} is not executable"
    subprocess.run(["bash", "-n", str(path)], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck():
    subprocess.run(["shellcheck", "-S", "warning", *[str(DEPLOY / s) for s in SCRIPTS]], check=True)


def test_caddyfile_key_lines():
    text = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "demo.ianduffy.com {" in text
    assert "reverse_proxy 127.0.0.1:8765" in text
    assert "@admin path /admin /admin/* /api/admin /api/admin/*" in text
    assert "basic_auth @admin" in text and "caddy hash-password" in text
    assert "encode gzip" in text
    # the event stream is proxied without compression and flushed at once
    events = text.split("handle /api/events*", 1)[1].split("handle {", 1)[0]
    assert "flush_interval -1" in events and "encode" not in events
    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy no-referrer"):
        assert header in text
    assert text.count("{") == text.count("}")


def test_service_unit_key_lines():
    text = (DEPLOY / "write-listen.service").read_text(encoding="utf-8")
    for line in ("User=writelisten", "WorkingDirectory=/srv/write-listen", "EnvironmentFile=/etc/write-listen/env",
                 "ExecStart=/srv/write-listen/.venv/bin/python server.py --host 127.0.0.1 --port 8765",
                 "Restart=on-failure", "StandardOutput=journal", "WantedBy=multi-user.target"):
        assert line in text.splitlines(), line


def test_daily_log_timer():
    timer = (DEPLOY / "write-listen-log.timer").read_text(encoding="utf-8")
    assert "OnCalendar=" in timer and "Persistent=true" in timer
    assert "ExecStart=/srv/write-listen/deploy/daily-log.sh" in (DEPLOY / "write-listen-log.service").read_text()
    assert "app-$day.log" in (DEPLOY / "daily-log.sh").read_text()


def test_env_example_has_every_key_and_no_secret():
    lines = (DEPLOY / "env.example").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if re.match(r"^[A-Z_]+=", line))
    for key in ("OPENAI_API_KEY", "WRITE_LISTEN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ADMIN_USER",
                "ADMIN_PASSWORD", "WL_BASE_URL", "TZ", "WL_DEMO"):
        assert key in values, key
    for key in ("OPENAI_API_KEY", "WRITE_LISTEN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ADMIN_PASSWORD"):
        assert values[key] == "", f"{key} must be empty in the example"
    assert values["WL_DEMO"] == "1"


def test_setup_and_deploy_use_the_same_layout():
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    deploy = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert "APP=/srv/write-listen" in setup and "USER_NAME=writelisten" in setup
    assert "install -m 600" in setup  # the env file
    assert "uv sync --no-dev --frozen" in setup and "uv sync --no-dev --frozen" in deploy
    assert "ufw allow 80/tcp" in setup and "ufw allow 443" in setup and "ufw allow OpenSSH" in setup
    assert "deploy/check.sh" in setup and "deploy/check.sh" in deploy
    assert "mkdir -p listeners shared" in deploy


def test_deploy_md_agrees_with_the_scripts():
    """DEPLOY.md's commands match what the scripts do (review 13)."""
    doc = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
    setup = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    rate = next(line for line in doc.splitlines() if line.startswith("| Rate limits"))
    # chapter builds print to their run.log (server.Pipeline._launch), not the service's journal
    assert "journalctl -u write-listen" in rate and "/srv/write-listen/listeners/*/chapters/*/run.log" in rate
    assert "StrictHostKeyChecking=accept-new" in doc  # step 7: the backup's first run to the domain
    # Caddy's config is validated as Caddy's user, as setup.sh does
    assert "sudo -u caddy caddy validate --adapter caddyfile" in setup
    assert "sudo -u caddy caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile" in doc
    assert 'ln -sfn "$ENV_FILE" "$APP/.env"' in setup and "/srv/write-listen/.env" in doc
    assert "Tester mode is OFF" in (DEPLOY / "check.sh").read_text() and '"Tester mode is OFF"' in doc
    assert "git -C /srv/write-listen fetch origin" in doc


def test_backup_plist_parses():
    plist = plistlib.loads((DEPLOY / "local.write-listen.backup.plist").read_bytes())
    assert plist["ProgramArguments"][0] == "/bin/bash"
    assert plist["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}


# --- render_caddy.py ----------------------------------------------------------------------------------

def _render(env, tmp_path):
    return subprocess.run([sys.executable, str(DEPLOY / "render_caddy.py"), str(DEPLOY / "Caddyfile")],
                          env={**os.environ, **env}, capture_output=True, text=True)


def test_render_fills_domain_and_login(tmp_path):
    out = _render({"WL_DOMAIN": "listen.example.com", "WL_ADMIN_USER": "ian", "WL_ADMIN_HASH": HASH}, tmp_path)
    assert out.returncode == 0, out.stderr
    assert "listen.example.com {" in out.stdout and "demo.ianduffy.com {" not in out.stdout
    assert f"\t\tian {HASH}\n" in out.stdout
    assert "REPLACE_WITH" not in out.stdout


@pytest.mark.parametrize("env", [
    {"WL_DOMAIN": "listen.example.com", "WL_ADMIN_USER": "ian", "WL_ADMIN_HASH": ""},
    {"WL_DOMAIN": "listen.example.com", "WL_ADMIN_USER": "ian", "WL_ADMIN_HASH": "plaintext"},
    {"WL_DOMAIN": "listen.example.com/x", "WL_ADMIN_USER": "ian", "WL_ADMIN_HASH": HASH},
    {"WL_DOMAIN": "listen.example.com", "WL_ADMIN_USER": "ian }", "WL_ADMIN_HASH": HASH},
])
def test_render_refuses_bad_values(env, tmp_path):
    out = _render(env, tmp_path)
    assert out.returncode == 1 and out.stdout == ""


@pytest.mark.skipif(not shutil.which("caddy"), reason="caddy not installed")
def test_rendered_caddyfile_validates(tmp_path):
    out = _render({"WL_DOMAIN": "listen.example.com", "WL_ADMIN_USER": "ian", "WL_ADMIN_HASH": HASH}, tmp_path)
    conf = tmp_path / "Caddyfile"
    conf.write_text(out.stdout.replace("/var/log/caddy/", f"{tmp_path}/"))
    subprocess.run(["caddy", "validate", "--adapter", "caddyfile", "--config", str(conf)], check=True)


# --- backup.sh ----------------------------------------------------------------------------------------

def test_backup_copies_both_folders_and_links_to_the_night_before(tmp_path):
    bin_dir, dest = tmp_path / "bin", tmp_path / "backups"
    bin_dir.mkdir()
    log = tmp_path / "rsync.log"
    (bin_dir / "rsync").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    (bin_dir / "rsync").chmod(0o755)
    (dest / "listeners" / "2026-01-01").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WL_BACKUPS": str(dest),
           "WL_DROPLET": "root@droplet.test"}
    subprocess.run(["bash", str(DEPLOY / "backup.sh")], env=env, check=True, capture_output=True)
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    listeners, shared = calls
    assert "root@droplet.test:/srv/write-listen/listeners/" in listeners
    assert f"--link-dest={dest}/listeners/2026-01-01" in listeners
    assert "root@droplet.test:/srv/write-listen/shared/" in shared and "--link-dest" not in shared
    assert "BatchMode=yes" in listeners
    # the first run to the domain (only the IP was ssh'd to) records the host key instead of failing (review 11)
    assert "StrictHostKeyChecking=accept-new" in listeners and "StrictHostKeyChecking=accept-new" in shared


# --- check.sh: tester mode must be on -----------------------------------------------------------------

def _serve(status_code):
    """A stand-in app on a free loopback port: / answers 200, /api/status answers `status_code`."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/" else status_code)
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.mark.parametrize("status_code, ok", [(404, True), (200, False)])
def test_check_stops_an_app_open_to_anyone(tmp_path, status_code, ok):
    httpd = _serve(status_code)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    (bin_dir / "sudo").write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')  # stands in for sudo systemctl
    (bin_dir / "sudo").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WL_PORT": str(httpd.server_port)}
    try:
        out = subprocess.run(["bash", str(DEPLOY / "check.sh")], env=env, capture_output=True, text=True)
    finally:
        httpd.shutdown()
    if ok:
        assert out.returncode == 0, out.stderr
        assert not log.exists()
    else:
        assert out.returncode == 1 and "Tester mode is OFF" in out.stderr
        assert log.read_text().strip() == "systemctl stop write-listen"


def test_setup_makes_caddys_data_folder_before_caddy_needs_it():
    # 2026-11-21: on the droplet /var/lib/caddy was missing, so Caddy could store no certificate
    # ("failed storage check: mkdir /var/lib/caddy: permission denied") and the site had no HTTPS.
    text = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    made = text.index("install -d -o caddy -g caddy -m 750 /var/lib/caddy")
    assert made < text.index('say "caddy config"')

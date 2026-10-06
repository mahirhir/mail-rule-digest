"""Exit codes and error messages for config, auth and network failures (fakes only, no network)."""

import imaplib
import io
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from mail_rule_digest import cli, digest, sources

FIXTURES = Path(__file__).parent / "fixtures"
RULES = Path(__file__).resolve().parent.parent / "examples" / "rules.toml"
SECRET = "s3cr3t-pa55word-XYZ"


@pytest.fixture
def imap_env(monkeypatch):
    for key in ("IMAP_ACCESS_TOKEN", "IMAP_TOKEN_COMMAND", "IMAP_PORT", "WEBHOOK_URL", "WEBHOOK_FORMAT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IMAP_HOST", "imap.example")
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", SECRET)
    return monkeypatch


def run_imap(tmp_path, *extra):
    return cli.main(["--rules", str(RULES), "--out", str(tmp_path / "d.md"), *extra])


def run_eml(tmp_path, *extra):
    return cli.main(["--rules", str(RULES), "--eml-dir", str(FIXTURES), "--out", str(tmp_path / "d.md"), *extra])


def test_constants():
    assert (cli.EXIT_OK, cli.EXIT_CONFIG, cli.EXIT_AUTH, cli.EXIT_NETWORK) == (0, 2, 3, 4)


def test_login_rejected_exits_3_without_secret(imap_env, tmp_path, capsys):
    class RejectingIMAP:
        def __init__(self, *a, **k):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] bad login {password}")

        def logout(self):
            pass

    imap_env.setattr(sources.imaplib, "IMAP4_SSL", RejectingIMAP)
    assert run_imap(tmp_path) == 3
    err = capsys.readouterr().err
    assert err.startswith("error: auth:") and "hint" in err
    assert SECRET not in err


@pytest.mark.parametrize("exc", [OSError("connection refused"), TimeoutError("timed out")])
def test_connect_failure_exits_4(imap_env, tmp_path, capsys, exc):
    def boom(*a, **k):
        raise exc

    imap_env.setattr(sources.imaplib, "IMAP4_SSL", boom)
    assert run_imap(tmp_path) == 4
    err = capsys.readouterr().err
    assert err.startswith("error: network:") and SECRET not in err


def test_drop_during_session_exits_4(imap_env, tmp_path, capsys):
    class DroppingIMAP:
        def __init__(self, *a, **k):
            pass

        def login(self, user, password):
            pass

        def select(self, folder, readonly):
            raise TimeoutError("read timed out")

        def logout(self):
            pass

    imap_env.setattr(sources.imaplib, "IMAP4_SSL", DroppingIMAP)
    assert run_imap(tmp_path) == 4
    assert capsys.readouterr().err.startswith("error: network:")


def test_missing_imap_host_exits_2(imap_env, tmp_path, capsys):
    imap_env.delenv("IMAP_HOST")
    assert run_imap(tmp_path) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: config:") and "IMAP_HOST" in err


def test_bad_imap_port_exits_2(imap_env, tmp_path, capsys):
    imap_env.setenv("IMAP_PORT", "ninety-three")
    assert run_imap(tmp_path) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: config:") and "IMAP_PORT" in err


def test_bad_eml_dir_exits_2(tmp_path, capsys):
    code = cli.main(["--rules", str(RULES), "--eml-dir", str(tmp_path / "nope")])
    assert code == 2
    assert "error: config:" in capsys.readouterr().err


def test_malformed_toml_exits_2(tmp_path, capsys):
    bad = tmp_path / "rules.toml"
    bad.write_text("[[rule\nname = ", encoding="utf-8")
    assert cli.main(["--rules", str(bad), "--eml-dir", str(FIXTURES)]) == 2
    assert "error: config:" in capsys.readouterr().err


def test_token_command_failure_exits_3(imap_env, tmp_path, capsys):
    imap_env.setenv("IMAP_TOKEN_COMMAND", "definitely-not-a-real-program-xyz")
    assert run_imap(tmp_path) == 3
    assert capsys.readouterr().err.startswith("error: auth:")


def _webhook_run(monkeypatch, tmp_path, url, fake):
    monkeypatch.setenv("WEBHOOK_URL", url)
    monkeypatch.delenv("WEBHOOK_FORMAT", raising=False)
    monkeypatch.setattr(digest.urllib.request, "urlopen", fake)
    return run_eml(tmp_path, "--webhook")


def test_webhook_url_error_exits_4(monkeypatch, tmp_path, capsys):
    def boom(req, timeout=None):
        raise URLError(OSError("name resolution failed"))

    assert _webhook_run(monkeypatch, tmp_path, "https://hooks.example/secret-path", boom) == 4
    err = capsys.readouterr().err
    assert "error: network:" in err and "secret-path" not in err


def test_webhook_timeout_exits_4(monkeypatch, tmp_path, capsys):
    def boom(req, timeout=None):
        raise TimeoutError("timed out")

    assert _webhook_run(monkeypatch, tmp_path, "https://hooks.example/x", boom) == 4
    assert "error: network:" in capsys.readouterr().err


def test_webhook_403_exits_3(monkeypatch, tmp_path, capsys):
    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

    assert _webhook_run(monkeypatch, tmp_path, "https://hooks.example/secret-path", boom) == 3
    err = capsys.readouterr().err
    assert "error: auth:" in err and "403" in err and "secret-path" not in err


def test_webhook_500_exits_4(monkeypatch, tmp_path, capsys):
    def boom(req, timeout=None):
        raise HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b""))

    assert _webhook_run(monkeypatch, tmp_path, "https://hooks.example/x", boom) == 4
    assert "error: network:" in capsys.readouterr().err


def test_http_webhook_url_exits_2(monkeypatch, tmp_path, capsys):
    def never(req, timeout=None):
        raise AssertionError("must not be called")

    assert _webhook_run(monkeypatch, tmp_path, "http://hooks.example/x", never) == 2
    assert "error: config:" in capsys.readouterr().err

from __future__ import annotations

import base64
import imaplib
import sys

import pytest
from test_digest import FakeIMAP

from mail_rule_digest import sources
from mail_rule_digest.sources import SourceError, xoauth2_string

TOKEN = "ya29.SECRET-TOKEN-VALUE"


class OAuthIMAP(FakeIMAP):
    fail = False

    def authenticate(self, mechanism, authobject):
        self.calls.append(("authenticate", mechanism))
        self.initial = authobject(b"")
        if self.fail:
            # imaplib answers the server's JSON error challenge with the callback again.
            self.second = authobject(b"eyJzdGF0dXMiOiI0MDEifQ==")
            raise imaplib.IMAP4.error("AUTHENTICATE failed")
        return "OK", [b"done"]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(sources.imaplib, "IMAP4_SSL", OAuthIMAP)
    OAuthIMAP.instances.clear()
    OAuthIMAP.fail = False
    for k in ("IMAP_PASSWORD", "IMAP_ACCESS_TOKEN", "IMAP_TOKEN_COMMAND"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IMAP_HOST", "imap.example")
    monkeypatch.setenv("IMAP_USER", "me@example.com")
    return monkeypatch


def cmd(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_sasl_string_format():
    assert xoauth2_string("u@x", "tok") == b"user=u@x\x01auth=Bearer tok\x01\x01"
    # imaplib base64-encodes the callback output; the wire form must round-trip.
    assert base64.b64decode(base64.b64encode(xoauth2_string("u@x", "tok"))).endswith(b"tok\x01\x01")


def test_token_env_uses_xoauth2_not_login(env):
    env.setenv("IMAP_ACCESS_TOKEN", f"  {TOKEN}\n")
    sources.fetch_imap(days=1, limit=1)
    inst = OAuthIMAP.instances[0]
    assert ("authenticate", "XOAUTH2") in inst.calls
    assert not any(c[0] == "login" for c in inst.calls)
    assert inst.initial == xoauth2_string("me@example.com", TOKEN)


def test_token_command_stdout_is_the_token(env):
    env.setenv("IMAP_TOKEN_COMMAND", cmd(f"print('{TOKEN}')"))
    sources.fetch_imap(days=1, limit=1)
    assert OAuthIMAP.instances[0].initial == xoauth2_string("me@example.com", TOKEN)


def test_password_auth_unchanged_and_selection(env):
    env.setenv("IMAP_PASSWORD", "pw")
    sources.fetch_imap(days=1, limit=1)
    calls = OAuthIMAP.instances[0].calls
    assert ("login", "me@example.com") in calls
    assert not any(c[0] == "authenticate" for c in calls)


def test_token_wins_over_password(env):
    env.setenv("IMAP_PASSWORD", "pw")
    env.setenv("IMAP_ACCESS_TOKEN", TOKEN)
    sources.fetch_imap(days=1, limit=1)
    assert ("authenticate", "XOAUTH2") in OAuthIMAP.instances[0].calls


def test_no_credentials_names_password_var(env):
    with pytest.raises(SourceError, match="IMAP_PASSWORD"):
        sources.fetch_imap(days=1, limit=1)


def test_token_command_nonzero_exit(env):
    env.setenv(
        "IMAP_TOKEN_COMMAND", cmd(f"import sys; print('{TOKEN}'); print('{TOKEN}', file=sys.stderr); sys.exit(3)")
    )
    with pytest.raises(SourceError, match="status 3") as ei:
        sources.fetch_imap(days=1, limit=1)
    assert TOKEN not in str(ei.value)


def test_token_command_empty_output(env):
    env.setenv("IMAP_TOKEN_COMMAND", cmd("pass"))
    with pytest.raises(SourceError, match="no token"):
        sources.fetch_imap(days=1, limit=1)


def test_token_command_timeout(env, monkeypatch):
    monkeypatch.setattr(
        sources.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            sources.subprocess.TimeoutExpired(a[0], k["timeout"], output=TOKEN.encode())
        ),
    )
    env.setenv("IMAP_TOKEN_COMMAND", "whatever")
    with pytest.raises(SourceError, match="timed out") as ei:
        sources.fetch_imap(days=1, limit=1)
    assert TOKEN not in str(ei.value)


def test_token_command_missing_program(env):
    env.setenv("IMAP_TOKEN_COMMAND", "definitely-not-a-real-program-xyz")
    with pytest.raises(SourceError, match="could not run"):
        sources.fetch_imap(days=1, limit=1)


def test_failed_auth_does_not_leak_token(env, capsys):
    env.setenv("IMAP_ACCESS_TOKEN", TOKEN)
    OAuthIMAP.fail = True
    with pytest.raises(SourceError) as ei:
        sources.fetch_imap(days=1, limit=1)
    inst = OAuthIMAP.instances[0]
    assert inst.second == b""
    assert TOKEN not in str(ei.value) and TOKEN not in repr(ei.value.__cause__)
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err

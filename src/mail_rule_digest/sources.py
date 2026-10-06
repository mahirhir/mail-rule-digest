"""Where messages come from: an IMAP mailbox (read-only) or a folder of .eml files."""

from __future__ import annotations

import contextlib
import imaplib
import os
import shlex
import ssl
import subprocess
from datetime import date, timedelta
from pathlib import Path

from .message import Message, parse_message


class SourceError(RuntimeError):
    """Raised when the mailbox cannot be reached or configured."""


class ConfigError(SourceError):
    """Missing or invalid configuration (environment variables, --eml-dir)."""


class AuthError(SourceError):
    """The server or the token command rejected the credentials."""


class NetworkError(SourceError):
    """The server could not be reached, or the connection failed during the session."""


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise ConfigError(f"environment variable {name} is not set (hint: export it, see the README quick start)")
    return value


def _scrub(text: str, secret: str) -> str:
    """Never let a password or token appear in an error message."""
    return text.replace(secret, "***") if secret else text


def xoauth2_string(user: str, token: str) -> bytes:
    """SASL XOAUTH2 initial response: user=<u>^Aauth=Bearer <token>^A^A (imaplib base64-encodes it)."""
    return f"user={user}auth=Bearer {token}".encode()


def _token_from_command(command: str) -> str:
    """Run the user's token command (no shell); its stdout is the access token."""
    try:
        argv = shlex.split(command)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        raise AuthError("IMAP_TOKEN_COMMAND timed out after 30s (hint: run the command by hand)") from None
    except (OSError, ValueError) as exc:
        raise AuthError(
            f"IMAP_TOKEN_COMMAND could not run: {type(exc).__name__} (hint: check the command and its path)"
        ) from None
    # Neither stdout nor stderr is echoed: either may contain the token.
    if proc.returncode != 0:
        raise AuthError(f"IMAP_TOKEN_COMMAND exited with status {proc.returncode} (hint: re-authorise the token tool)")
    token = proc.stdout.strip()
    if not token:
        raise AuthError("IMAP_TOKEN_COMMAND printed no token (hint: it must print the access token on stdout)")
    return token


def _auth_secret() -> tuple[str, str]:
    """Pick the auth method: ('xoauth2', token) if a token is configured, else ('password', pw)."""
    token = os.environ.get("IMAP_ACCESS_TOKEN")
    if token and token.strip():
        return "xoauth2", token.strip()
    command = os.environ.get("IMAP_TOKEN_COMMAND")
    if command and command.strip():
        return "xoauth2", _token_from_command(command)
    return "password", _env("IMAP_PASSWORD")


def fetch_imap(days: int, limit: int, since_date: date | None = None) -> list[Message]:
    """Fetch messages from the last `days` days (or on/after `since_date`) without changing any flags."""
    host = _env("IMAP_HOST")
    user = _env("IMAP_USER")
    method, secret = _auth_secret()
    try:
        port = int(_env("IMAP_PORT", "993"))
    except ValueError:
        raise ConfigError("IMAP_PORT must be an integer (hint: the usual value is 993)") from None
    folder = _env("IMAP_FOLDER", "INBOX")
    start = since_date or (date.today() - timedelta(days=max(days - 1, 0)))
    since = start.strftime("%d-%b-%Y")

    try:
        # imaplib's own default context skips certificate checks, so pass a verifying one.
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)
    except OSError as exc:
        raise NetworkError(
            f"cannot connect to {host}:{port}: {exc} (hint: check IMAP_HOST, IMAP_PORT, network)"
        ) from exc
    try:
        if method == "xoauth2":
            sent: list[bool] = []

            def _respond(_challenge: bytes) -> bytes:
                # A second challenge is the server's JSON error; answer empty to finish cleanly.
                first = not sent
                sent.append(True)
                return xoauth2_string(user, secret) if first else b""

            try:
                conn.authenticate("XOAUTH2", _respond)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error as exc:
                raise AuthError(_scrub(f"login rejected: {exc} (hint: the token may be expired)", secret)) from exc
        else:
            try:
                conn.login(user, secret)
            except imaplib.IMAP4.abort:
                raise
            except imaplib.IMAP4.error as exc:
                hint = "hint: check IMAP_USER and IMAP_PASSWORD (or app password)"
                raise AuthError(_scrub(f"login rejected: {exc} ({hint})", secret)) from exc
        status, _ = conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise SourceError(f"cannot open folder {folder!r}")
        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            raise SourceError("IMAP SEARCH failed")
        ids = data[0].split()[-limit:] if data and data[0] else []
        messages: list[Message] = []
        for msg_id in ids:
            status, parts = conn.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for part in parts:
                if isinstance(part, tuple):
                    messages.append(parse_message(part[1]))
        return messages
    except imaplib.IMAP4.abort as exc:
        raise NetworkError(f"connection lost: {exc} (hint: retry later)") from exc
    except imaplib.IMAP4.error as exc:
        raise SourceError(_scrub(f"IMAP error: {exc}", secret)) from exc
    except OSError as exc:
        raise NetworkError(f"connection failed: {exc} (hint: check the network and retry)") from exc
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            conn.logout()


def read_eml_dir(path: Path) -> list[Message]:
    if not path.is_dir():
        raise ConfigError(f"{path} is not a directory (hint: check --eml-dir)")
    return [parse_message(p.read_bytes()) for p in sorted(path.glob("*.eml"))]

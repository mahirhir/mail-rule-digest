"""Where messages come from: an IMAP mailbox (read-only) or a folder of .eml files."""

from __future__ import annotations

import contextlib
import imaplib
import os
import ssl
from datetime import date, timedelta
from pathlib import Path

from .message import Message, parse_message


class SourceError(RuntimeError):
    """Raised when the mailbox cannot be reached or configured."""


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise SourceError(f"environment variable {name} is not set")
    return value


def fetch_imap(days: int, limit: int) -> list[Message]:
    """Fetch messages from the last `days` days without changing any flags."""
    host = _env("IMAP_HOST")
    user = _env("IMAP_USER")
    password = _env("IMAP_PASSWORD")
    port = int(_env("IMAP_PORT", "993"))
    folder = _env("IMAP_FOLDER", "INBOX")
    since = (date.today() - timedelta(days=max(days - 1, 0))).strftime("%d-%b-%Y")

    try:
        # imaplib's own default context skips certificate checks, so pass a verifying one.
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)
    except OSError as exc:
        raise SourceError(f"cannot connect to {host}:{port}: {exc}") from exc
    try:
        conn.login(user, password)
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
    except imaplib.IMAP4.error as exc:
        raise SourceError(f"IMAP error: {exc}") from exc
    finally:
        with contextlib.suppress(imaplib.IMAP4.error, OSError):
            conn.logout()


def read_eml_dir(path: Path) -> list[Message]:
    if not path.is_dir():
        raise SourceError(f"{path} is not a directory")
    return [parse_message(p.read_bytes()) for p in sorted(path.glob("*.eml"))]

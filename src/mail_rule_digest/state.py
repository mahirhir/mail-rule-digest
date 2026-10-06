"""Remember which messages were already reported, so repeated runs never report one twice."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from .message import Message

STATE_VERSION = 1
PRUNE_DAYS = 180


class StateError(ValueError):
    """Raised when the state file is unreadable or has the wrong shape."""


def message_key(msg: Message) -> str:
    """A stable key: the Message-ID, else a sha256 over sender, subject, date and the start of the body."""
    if msg.message_id:
        return msg.message_id
    when = msg.date.isoformat() if msg.date else ""
    raw = "|".join((msg.sender_addr, msg.subject, when, msg.body[:200]))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_seen(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StateError(f"cannot read state file {path}: {exc}") from exc
    seen = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(seen, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in seen.items()):
        raise StateError(f"state file {path} has an unexpected shape (expected {{'version': 1, 'seen': {{...}}}})")
    for stamp in seen.values():
        try:
            date.fromisoformat(stamp)
        except ValueError as exc:
            raise StateError(f"state file {path} has an invalid date {stamp!r}") from exc
    return seen


def load_state(path: Path) -> set[str]:
    """The set of seen keys; a missing file means an empty set."""
    return set(_read_seen(path))


def save_state(path: Path, keys: Iterable[str], today: date | None = None) -> None:
    """Write the state atomically, keeping first-seen dates and pruning entries older than 180 days."""
    today = today or date.today()
    try:
        old = _read_seen(path)
    except StateError:
        old = {}
    cutoff = today - timedelta(days=PRUNE_DAYS)
    seen: dict[str, str] = {}
    for key in keys:
        stamp = old.get(key, today.isoformat())
        if date.fromisoformat(stamp) >= cutoff:
            seen[key] = stamp
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": STATE_VERSION, "seen": seen}, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)

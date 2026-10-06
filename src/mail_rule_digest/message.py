"""Parse raw RFC 5322 bytes into the few fields the rules look at."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime

_TAG = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.S | re.I)
_SPACE = re.compile(r"[ \t\r\f\v]+")


@dataclass(frozen=True)
class Message:
    sender: str
    sender_addr: str
    subject: str
    date: datetime | None
    body: str
    message_id: str = ""


def _html_to_text(markup: str) -> str:
    # ponytail: regex tag strip, good enough for keyword and number search; use html.parser if layout matters.
    return html.unescape(_TAG.sub(" ", markup))


def _body_text(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", errors="replace")
    if part.get_content_subtype() == "html":
        text = _html_to_text(text)
    return "\n".join(_SPACE.sub(" ", line).strip() for line in text.splitlines()).strip()


def parse_message(raw: bytes) -> Message:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    assert isinstance(msg, EmailMessage)
    sender = str(msg.get("From", ""))
    try:
        date = parsedate_to_datetime(str(msg["Date"])) if msg["Date"] else None
    except (TypeError, ValueError):
        date = None
    return Message(
        sender=sender,
        sender_addr=parseaddr(sender)[1].lower(),
        subject=str(msg.get("Subject", "")).strip(),
        date=date,
        body=_body_text(msg),
        message_id=str(msg.get("Message-ID", "")).strip(),
    )

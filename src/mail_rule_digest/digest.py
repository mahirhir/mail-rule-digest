"""Render matches as Markdown and post them to a chat webhook."""

from __future__ import annotations

import json
import urllib.request
from datetime import date
from urllib.parse import urlparse

from .rules import Match, Rule

# Discord rejects content over 2000 characters; Slack's limit is far higher.
DISCORD_LIMIT = 2000
SLACK_LIMIT = 39000
# Teams messages are capped near 28 KB including the card; leave headroom for the JSON wrapper.
TEAMS_LIMIT = 20000
TEAMS_HOSTS = (".logic.azure.com", ".powerplatform.com", ".webhook.office.com")
WEBHOOK_FORMATS = ("auto", "discord", "slack", "teams")


def _clean(text: str, limit: int = 120) -> str:
    text = " ".join(text.split()).replace("[", "\\[").replace("]", "\\]")
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _fmt(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"


def render(rules: list[Rule], matches: list[Match], day: date) -> str:
    lines = [f"# Mail digest {day.isoformat()}", ""]
    lines.append(f"{len(matches)} matching message(s) across {len(rules)} rule(s).")
    for rule in rules:
        hits = [m for m in matches if m.rule is rule]
        lines += ["", f"## {rule.name} ({len(hits)})", ""]
        if not hits:
            lines.append("_No matches._")
            continue
        for m in sorted(hits, key=lambda h: h.message.date.timestamp() if h.message.date else 0):
            when = m.message.date.strftime("%Y-%m-%d %H:%M") if m.message.date else "no date"
            line = f"- **{_clean(m.message.subject) or '(no subject)'}** - {_clean(m.message.sender_addr, 60)}, {when}"
            if m.values:
                line += " - " + ", ".join(f"{k}: {_fmt(v)}" for k, v in m.values.items())
            lines.append(line)
        if len(hits) > 1 and hits[0].values:
            for name in hits[0].values:
                total = sum(m.values.get(name, 0.0) for m in hits)
                lines.append(f"- _total {name}: {_fmt(total)}_")
    return "\n".join(lines) + "\n"


def _cut(markdown: str, limit: int) -> str:
    return markdown if len(markdown) <= limit else markdown[: limit - 4] + "\n..."


def detect_format(url: str) -> str:
    """Pick a webhook format from the host: discord, teams, or slack-style text."""
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("discord.com") or host.endswith("discordapp.com"):
        return "discord"
    if host.endswith(TEAMS_HOSTS):
        return "teams"
    return "slack"


def webhook_payload(url: str, markdown: str, fmt: str = "auto") -> dict[str, object]:
    if fmt not in WEBHOOK_FORMATS:
        raise ValueError(f"unknown webhook format {fmt!r} (choose from {', '.join(WEBHOOK_FORMATS)})")
    explicit = fmt != "auto"
    if not explicit:
        fmt = detect_format(url)
    if fmt == "discord":
        # An email subject containing @everyone must not ping the channel.
        return {"content": _cut(markdown, DISCORD_LIMIT), "allowed_mentions": {"parse": []}}
    if fmt == "teams":
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [{"type": "TextBlock", "text": _cut(markdown, TEAMS_LIMIT), "wrap": True}],
        }
        attachment = {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}
        return {"type": "message", "attachments": [attachment]}
    text = _cut(markdown, SLACK_LIMIT)
    # Auto keeps the plain {"text": ...} body; an explicit slack choice adds mrkdwn.
    return {"text": text, "mrkdwn": True} if explicit else {"text": text}


def post_webhook(url: str, markdown: str, timeout: float = 15, fmt: str = "auto") -> int:
    if urlparse(url).scheme != "https":
        raise ValueError("webhook URL must use https")
    body = json.dumps(webhook_payload(url, markdown, fmt)).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "mail-rule-digest"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(resp.status)

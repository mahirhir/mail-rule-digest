"""Webhook payload formats and their selection from the CLI (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mail_rule_digest import cli, digest

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "examples" / "rules.toml"
FIXTURES = ROOT / "tests" / "fixtures"

DISCORD_URL = "https://discord.com/api/webhooks/1/x"
TEAMS_URL = "https://prod-1.westus.logic.azure.com/workflows/abc"
SLACK_URL = "https://hooks.slack.com/services/T/B/x"


def test_formats_constant():
    assert digest.WEBHOOK_FORMATS == ("auto", "discord", "slack", "teams")


def test_discord_shape():
    assert digest.webhook_payload(SLACK_URL, "hi", "discord") == {"content": "hi", "allowed_mentions": {"parse": []}}


def test_slack_shape():
    assert digest.webhook_payload(DISCORD_URL, "hi", "slack") == {"text": "hi", "mrkdwn": True}


def test_teams_shape():
    p = digest.webhook_payload(SLACK_URL, "# hi", "teams")
    assert p["type"] == "message"
    (att,) = p["attachments"]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert att["contentUrl"] is None
    card = att["content"]
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert card["type"] == "AdaptiveCard"
    assert card["version"] == "1.4"
    assert card["body"] == [{"type": "TextBlock", "text": "# hi", "wrap": True}]
    json.dumps(p)


@pytest.mark.parametrize(
    ("url", "key"),
    [
        (DISCORD_URL, "content"),
        ("https://discordapp.com/api/webhooks/1/x", "content"),
        (TEAMS_URL, "attachments"),
        ("https://x.environment.api.powerplatform.com/y", "attachments"),
        ("https://contoso.webhook.office.com/webhookb2/z", "attachments"),
        (SLACK_URL, "text"),
        ("https://chat.example.org/hook", "text"),
    ],
)
def test_auto_detection(url, key):
    assert key in digest.webhook_payload(url, "hi")


def test_auto_other_host_unchanged():
    assert digest.webhook_payload(SLACK_URL, "hi") == {"text": "hi"}


def test_truncation():
    big = "x" * 100000
    assert len(digest.webhook_payload(DISCORD_URL, big)["content"]) == digest.DISCORD_LIMIT
    assert len(digest.webhook_payload(SLACK_URL, big, "slack")["text"]) == digest.SLACK_LIMIT
    teams = digest.webhook_payload(TEAMS_URL, big)
    text = teams["attachments"][0]["content"]["body"][0]["text"]
    assert len(text) == digest.TEAMS_LIMIT and text.endswith("...")


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        digest.webhook_payload(SLACK_URL, "hi", "bogus")


def test_http_still_rejected():
    with pytest.raises(ValueError):
        digest.post_webhook("http://example.org/h", "hi", fmt="slack")


class FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def run_cli(monkeypatch, tmp_path, url, *extra):
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return FakeResp()

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("WEBHOOK_URL", url)
    out = tmp_path / "d.md"
    argv = ["--rules", str(RULES), "--eml-dir", str(FIXTURES), "--out", str(out), "--webhook", *extra]
    return cli.main(argv), sent


def test_cli_flag_chooses_format(monkeypatch, tmp_path):
    monkeypatch.delenv("WEBHOOK_FORMAT", raising=False)
    rc, sent = run_cli(monkeypatch, tmp_path, SLACK_URL, "--webhook-format", "teams")
    assert rc == 0 and sent[0]["type"] == "message"


def test_cli_env_chooses_format(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_FORMAT", "slack")
    rc, sent = run_cli(monkeypatch, tmp_path, DISCORD_URL)
    assert rc == 0 and sent[0]["mrkdwn"] is True


def test_cli_flag_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_FORMAT", "slack")
    rc, sent = run_cli(monkeypatch, tmp_path, SLACK_URL, "--webhook-format", "discord")
    assert rc == 0 and "content" in sent[0]


def test_cli_default_is_auto(monkeypatch, tmp_path):
    monkeypatch.delenv("WEBHOOK_FORMAT", raising=False)
    rc, sent = run_cli(monkeypatch, tmp_path, TEAMS_URL)
    assert rc == 0 and "attachments" in sent[0]


def test_cli_invalid_env_format_exit_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WEBHOOK_FORMAT", "carrier-pigeon")
    rc, sent = run_cli(monkeypatch, tmp_path, SLACK_URL)
    assert rc == 2 and sent == []
    assert not (tmp_path / "d.md").exists()
    assert capsys.readouterr().err.startswith("error:")

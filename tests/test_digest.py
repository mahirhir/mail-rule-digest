from __future__ import annotations

import io
import json
import ssl
from datetime import date
from pathlib import Path

import pytest

from mail_rule_digest import cli, digest, sources
from mail_rule_digest.message import parse_message
from mail_rule_digest.rules import RuleError, apply_rules, load_rules

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
RULES = ROOT / "examples" / "rules.toml"


def fixture(name: str):
    return parse_message((FIXTURES / name).read_bytes())


def write_rules(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "rules.toml"
    p.write_text(text, encoding="utf-8")
    return p


# --- parsing ---------------------------------------------------------------


def test_parse_plain_message():
    msg = fixture("invoice_large.eml")
    assert msg.sender_addr == "billing@acme.example"
    assert msg.subject == "Invoice INV-1042 is ready"
    assert "Total: $1,240.50" in msg.body
    assert msg.date is not None and msg.date.year == 2026


def test_parse_html_only_message_strips_tags_and_style():
    msg = fixture("shipping_html.eml")
    assert msg.subject.endswith("\U0001f4e6")
    assert "<" not in msg.body
    assert "color:red" not in msg.body
    assert "TRK-77 & status: out for delivery" in msg.body


# --- rule loading ----------------------------------------------------------


def test_example_rules_load():
    rules = load_rules(RULES)
    assert [r.name for r in rules] == [
        "Invoices over $100",
        "Disk alerts at 90% or more",
        "Parcels out for delivery",
    ]


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("x = 1", "at least one [[rule]]"),
        ('[[rule]]\nsubject = "a"', "'name' is required"),
        ('[[rule]]\nname = "n"', "no conditions"),
        ('[[rule]]\nname = "n"\nsubject = "("', "invalid regex"),
        ('[[rule]]\nname = "n"\nsubjct = "a"', "unknown keys"),
        ('[[rule]]\nname = "n"\n[rule.numbers.amt]\npattern = "(?P<x>\\\\d+)"', "named group (?P<amt>"),
        ('[[rule]]\nname = "n"\n[rule.numbers.amt]\npattern = "(?P<amt>\\\\d+)"\nmin = "5"', "expected a number"),
        ("[[rule]\n", "rules.toml"),
    ],
)
def test_bad_rule_files_are_rejected(tmp_path, text, needle):
    with pytest.raises(RuleError, match=None) as exc:
        load_rules(write_rules(tmp_path, text))
    assert needle in str(exc.value)


# --- matching --------------------------------------------------------------


def all_fixtures():
    return [fixture(p.name) for p in sorted(FIXTURES.glob("*.eml"))]


def test_example_rules_select_the_right_messages():
    rules = load_rules(RULES)
    matches = apply_rules(rules, all_fixtures())
    got = sorted((m.rule.name, m.message.subject) for m in matches)
    assert got == [
        ("Disk alerts at 90% or more", "[ALERT] disk usage 93% on web-1 @everyone"),
        ("Invoices over $100", "Invoice INV-1042 is ready"),
        ("Parcels out for delivery", "Your parcel is out for delivery \U0001f4e6"),
    ]
    invoice = next(m for m in matches if m.rule.name.startswith("Invoices"))
    assert invoice.values == {"amount": 1240.50}


def test_numeric_bounds_are_inclusive_and_max_applies(tmp_path):
    base = "[[rule]]\nname = \"n\"\n[rule.numbers.amount]\npattern = '\\$(?P<amount>[\\d,.]+)'\n"
    msg = fixture("invoice_large.eml")
    assert apply_rules(load_rules(write_rules(tmp_path, base + "min = 1240.5\n")), [msg])
    assert not apply_rules(load_rules(write_rules(tmp_path, base + "max = 1000\n")), [msg])


def test_missing_number_means_no_match(tmp_path):
    rules = load_rules(
        write_rules(
            tmp_path, "[[rule]]\nname = \"n\"\n[rule.numbers.w]\npattern = 'Weight: (?P<w>\\d+(\\.\\d+)?) kg'\n"
        )
    )
    assert [m.values for m in apply_rules(rules, all_fixtures())] == [{"w": 2.5}]


def test_sender_glob_is_case_insensitive(tmp_path):
    rules = load_rules(write_rules(tmp_path, '[[rule]]\nname = "n"\nfrom = "*@ACME.example"\n'))
    assert len(apply_rules(rules, all_fixtures())) == 2


# --- rendering and webhook -------------------------------------------------


def test_render_lists_matches_totals_and_empty_rules():
    rules = load_rules(RULES)
    md = digest.render(rules, apply_rules(rules, all_fixtures()), date(2026, 10, 5))
    assert md.startswith("# Mail digest 2026-10-05\n")
    assert "3 matching message(s) across 3 rule(s)." in md
    assert "amount: 1,240.50" in md
    assert "_total" not in md
    assert "\\[ALERT\\]" in md
    md_empty = digest.render(rules, [], date(2026, 10, 5))
    assert md_empty.count("_No matches._") == 3


def test_render_totals_numbers_across_several_hits(tmp_path):
    rules = load_rules(
        write_rules(tmp_path, "[[rule]]\nname = \"n\"\n[rule.numbers.amount]\npattern = '\\$(?P<amount>[\\d,.]+)'\n")
    )
    md = digest.render(rules, apply_rules(rules, all_fixtures()), date(2026, 10, 5))
    assert "## n (2)" in md
    assert "_total amount: 1,252.50_" in md


def test_discord_payload_blocks_mentions_and_truncates():
    payload = digest.webhook_payload("https://discord.com/api/webhooks/1/x", "@everyone " + "a" * 5000)
    assert payload["allowed_mentions"] == {"parse": []}
    assert len(payload["content"]) <= digest.DISCORD_LIMIT


def test_slack_payload_uses_text():
    assert digest.webhook_payload("https://hooks.slack.com/services/x", "hi") == {"text": "hi"}


def test_post_webhook_refuses_plain_http():
    with pytest.raises(ValueError):
        digest.post_webhook("http://hooks.example/x", "hi")


def test_post_webhook_sends_json(monkeypatch):
    sent = {}

    class Resp(io.BytesIO):
        status = 204

    def fake_urlopen(req, timeout):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data)
        return Resp()

    monkeypatch.setattr(digest.urllib.request, "urlopen", fake_urlopen)
    assert digest.post_webhook("https://hooks.example/x", "hello") == 204
    assert sent == {"url": "https://hooks.example/x", "body": {"text": "hello"}}


# --- IMAP (fake server, no network) ----------------------------------------


class FakeIMAP:
    instances: list[FakeIMAP] = []

    def __init__(self, host, port, ssl_context, timeout):
        self.ssl_context = ssl_context
        self.calls: list[tuple] = [("connect", host, port)]
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, folder, readonly):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        self.calls.append(("search", *criteria))
        return "OK", [b"1 2"]

    def fetch(self, msg_id, spec):
        self.calls.append(("fetch", msg_id, spec))
        name = {b"1": "invoice_large.eml", b"2": "newsletter.eml"}[msg_id]
        return "OK", [(b"1 (BODY[] {n}", (FIXTURES / name).read_bytes()), b")"]

    def logout(self):
        self.calls.append(("logout",))


def test_imap_fetch_is_read_only(monkeypatch):
    monkeypatch.setattr(sources.imaplib, "IMAP4_SSL", FakeIMAP)
    for k, v in {"IMAP_HOST": "imap.example", "IMAP_USER": "u", "IMAP_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    FakeIMAP.instances.clear()
    msgs = sources.fetch_imap(days=1, limit=10)
    assert [m.sender_addr for m in msgs] == ["billing@acme.example", "news@letters.example"]
    ctx = FakeIMAP.instances[0].ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    calls = FakeIMAP.instances[0].calls
    assert ("select", '"INBOX"', True) in calls
    assert all(c[2] == "(BODY.PEEK[])" for c in calls if c[0] == "fetch")
    assert calls[-1] == ("logout",)


def test_imap_requires_env(monkeypatch):
    monkeypatch.delenv("IMAP_HOST", raising=False)
    with pytest.raises(sources.SourceError, match="IMAP_HOST"):
        sources.fetch_imap(days=1, limit=10)


# --- CLI -------------------------------------------------------------------


def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["--rules", str(RULES), "--eml-dir", str(FIXTURES), "--dry-run", "--webhook"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert "## Invoices over $100 (1)" in out
    assert "[dry-run] 5 scanned, 3 matched" in err
    assert list(tmp_path.iterdir()) == []


def test_cli_writes_digest_file(tmp_path):
    out = tmp_path / "d.md"
    assert cli.main(["--rules", str(RULES), "--eml-dir", str(FIXTURES), "--out", str(out)]) == 0
    assert "Parcels out for delivery (1)" in out.read_text(encoding="utf-8")


def test_cli_bad_rules_exit_2(tmp_path, capsys):
    bad = write_rules(tmp_path, "nope = 1")
    assert cli.main(["--rules", str(bad), "--eml-dir", str(FIXTURES)]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_webhook_without_url_exit_2(tmp_path, monkeypatch):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    out = tmp_path / "d.md"
    assert cli.main(["--rules", str(RULES), "--eml-dir", str(FIXTURES), "--out", str(out), "--webhook"]) == 2

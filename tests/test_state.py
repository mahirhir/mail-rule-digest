from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from urllib.error import URLError

import pytest

from mail_rule_digest import cli, digest, sources
from mail_rule_digest.message import Message, parse_message
from mail_rule_digest.state import StateError, load_state, message_key, save_state

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
RULES = ROOT / "examples" / "rules.toml"


@pytest.fixture
def eml_dir(tmp_path):
    d = tmp_path / "mail"
    shutil.copytree(FIXTURES, d)
    return d


def run(tmp_path, *extra, eml=None):
    out = tmp_path / "digest.md"
    argv = ["--rules", str(RULES), "--out", str(out), *extra]
    if eml is not None:
        argv += ["--eml-dir", str(eml)]
    code = cli.main(argv)
    return code, out


def test_second_run_reports_nothing(tmp_path, eml_dir):
    state = tmp_path / "state.json"
    code, out = run(tmp_path, "--state", str(state), eml=eml_dir)
    assert code == 0
    assert "3 matching message(s)" in out.read_text(encoding="utf-8")
    assert json.loads(state.read_text())["version"] == 1
    code, out = run(tmp_path, "--state", str(state), eml=eml_dir)
    assert code == 0
    assert "0 matching message(s)" in out.read_text(encoding="utf-8")


def test_new_eml_between_runs_is_the_only_one_reported(tmp_path, eml_dir):
    state = tmp_path / "state.json"
    run(tmp_path, "--state", str(state), eml=eml_dir)
    raw = (FIXTURES / "invoice_large.eml").read_text(encoding="utf-8")
    raw = raw.replace("inv-1042@acme.example", "inv-2000@acme.example").replace("INV-1042", "INV-2000")
    (eml_dir / "new.eml").write_text(raw, encoding="utf-8")
    code, out = run(tmp_path, "--state", str(state), eml=eml_dir)
    text = out.read_text(encoding="utf-8")
    assert code == 0
    assert "1 matching message(s)" in text and "INV-2000" in text
    assert "INV-1042" not in text


def test_since_filters_eml_dir_and_keeps_undated(tmp_path, eml_dir):
    (eml_dir / "undated.eml").write_text(
        "From: billing@acme.example\nSubject: Invoice undated\n\nTotal: $500.00 due soon\n", encoding="utf-8"
    )
    _, out = run(tmp_path, "--since", "2026-10-06", eml=eml_dir)
    text = out.read_text(encoding="utf-8")
    assert "Invoice undated" in text
    assert "INV-1042" not in text  # dated 2026-10-05


def test_bad_since_is_an_argparse_error(tmp_path, eml_dir):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "--since", "yesterday", eml=eml_dir)
    assert exc.value.code == 2


def test_dry_run_writes_no_state(tmp_path, eml_dir):
    state = tmp_path / "state.json"
    code, _ = run(tmp_path, "--state", str(state), "--dry-run", eml=eml_dir)
    assert code == 0
    assert not state.exists()


def test_failed_webhook_does_not_save_state(tmp_path, eml_dir, monkeypatch, capsys):
    state = tmp_path / "state.json"
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example/x")

    def boom(req, timeout):
        raise URLError("down")

    monkeypatch.setattr(digest.urllib.request, "urlopen", boom)
    code, _ = run(tmp_path, "--state", str(state), "--webhook", eml=eml_dir)
    assert code == 1
    assert "webhook failed" in capsys.readouterr().err
    assert not state.exists()


def test_corrupt_state_exits_2(tmp_path, eml_dir, capsys):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    code, _ = run(tmp_path, "--state", str(state), eml=eml_dir)
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "state.json" in err
    state.write_text('{"version": 1, "seen": [1]}', encoding="utf-8")
    code, _ = run(tmp_path, "--state", str(state), eml=eml_dir)
    assert code == 2


def test_load_state_missing_is_empty_and_errors_name_the_file(tmp_path):
    assert load_state(tmp_path / "none.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(StateError, match="bad.json"):
        load_state(bad)
    assert issubclass(StateError, ValueError)


def test_save_state_prunes_old_entries_and_is_atomic(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 1, "seen": {"old": "2025-01-01", "keep": "2026-09-30"}}), encoding="utf-8")
    save_state(path, ["old", "keep", "new"], today=date(2026, 10, 7))
    seen = json.loads(path.read_text())["seen"]
    assert seen == {"keep": "2026-09-30", "new": "2026-10-07"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_message_without_message_id_uses_fallback_key():
    raw = b"From: A <a@b.example>\nSubject: hi\nDate: Mon, 05 Oct 2026 09:15:00 +0000\n\nbody text\n"
    msg = parse_message(raw)
    assert msg.message_id == ""
    key = message_key(msg)
    assert key.startswith("sha256:") and len(key) == len("sha256:") + 64
    assert key == message_key(parse_message(raw))
    other = Message("A <a@b.example>", "a@b.example", "other", msg.date, "body text")
    assert message_key(other) != key
    with_id = parse_message(b"Message-ID:  <x@y>  \nSubject: s\n\nb\n")
    assert message_key(with_id) == "<x@y>"


# --- IMAP (fake server, no network) ----------------------------------------


class FakeIMAP:
    searches: list[tuple] = []

    def __init__(self, host, port, ssl_context, timeout):
        pass

    def login(self, user, password):
        pass

    def select(self, folder, readonly):
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        FakeIMAP.searches.append(criteria)
        return "OK", [b"1 2"]

    def fetch(self, msg_id, spec):
        name = {b"1": "invoice_large.eml", b"2": "alert_mentions.eml"}[msg_id]
        return "OK", [(b"1 (BODY[] {n}", (FIXTURES / name).read_bytes()), b")"]

    def logout(self):
        pass


def test_imap_two_runs_with_since(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.imaplib, "IMAP4_SSL", FakeIMAP)
    for k, v in {"IMAP_HOST": "imap.example", "IMAP_USER": "u", "IMAP_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    FakeIMAP.searches.clear()
    state = tmp_path / "state.json"
    code, out = run(tmp_path, "--since", "2026-10-03", "--state", str(state))
    assert code == 0
    assert "2 matching message(s)" in out.read_text(encoding="utf-8")
    code, out = run(tmp_path, "--since", "2026-10-03", "--state", str(state))
    assert code == 0
    assert "0 matching message(s)" in out.read_text(encoding="utf-8")
    assert FakeIMAP.searches == [("SINCE", "03-Oct-2026")] * 2

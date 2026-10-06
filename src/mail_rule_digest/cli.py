"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError

from . import __version__
from .digest import WEBHOOK_FORMATS, post_webhook, render
from .rules import RuleError, apply_rules, load_rules
from .sources import AuthError, NetworkError, SourceError, fetch_imap, read_eml_dir
from .state import StateError, load_state, message_key, save_state

# Process exit codes.
EXIT_OK = 0
EXIT_CONFIG = 2  # bad rules, environment, state file, webhook URL or format
EXIT_AUTH = 3  # mailbox login or token command rejected, webhook HTTP 401/403
EXIT_NETWORK = 4  # cannot reach the mailbox or the webhook


def _fail(kind: str, detail: str, code: int) -> int:
    print(f"error: {kind}: {detail}", file=sys.stderr)
    return code


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {value!r} (expected YYYY-MM-DD)") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mail-rule-digest",
        description="Filter an IMAP mailbox with a TOML rule file and write a Markdown digest.",
    )
    p.add_argument("--rules", type=Path, required=True, help="path to the TOML rule file")
    p.add_argument("--eml-dir", type=Path, help="read .eml files from a folder instead of IMAP")
    p.add_argument("--days", type=int, default=1, help="look back this many days (IMAP only, default 1)")
    p.add_argument("--since", type=_iso_date, metavar="YYYY-MM-DD", help="only messages on or after this date")
    p.add_argument("--state", type=Path, help="JSON file of already-reported messages; they are skipped")
    p.add_argument("--limit", type=int, default=500, help="fetch at most this many recent messages (default 500)")
    p.add_argument("--out", type=Path, help="write the digest here (default: digest-YYYY-MM-DD.md)")
    p.add_argument("--webhook", action="store_true", help="also POST the digest to $WEBHOOK_URL")
    p.add_argument(
        "--webhook-format",
        choices=WEBHOOK_FORMATS,
        help="webhook payload format (default: $WEBHOOK_FORMAT, then auto)",
    )
    p.add_argument("--dry-run", action="store_true", help="print the digest; write nothing, post nothing")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = date.today()
    webhook_format = args.webhook_format or os.environ.get("WEBHOOK_FORMAT") or "auto"
    if webhook_format not in WEBHOOK_FORMATS:
        return _fail(
            "config",
            f"unknown WEBHOOK_FORMAT {webhook_format!r} (choose from {', '.join(WEBHOOK_FORMATS)})",
            EXIT_CONFIG,
        )
    try:
        rules = load_rules(args.rules)
        if args.eml_dir:
            messages = read_eml_dir(args.eml_dir)
            if args.since:
                messages = [m for m in messages if m.date is None or m.date.date() >= args.since]
        else:
            messages = fetch_imap(args.days, args.limit, args.since)
        seen = load_state(args.state) if args.state else set()
    except AuthError as exc:
        return _fail("auth", str(exc), EXIT_AUTH)
    except NetworkError as exc:
        return _fail("network", str(exc), EXIT_NETWORK)
    except (RuleError, SourceError, StateError, OSError) as exc:
        return _fail("config", str(exc), EXIT_CONFIG)

    keys = {message_key(m) for m in messages}
    if args.state:
        messages = [m for m in messages if message_key(m) not in seen]
    matches = apply_rules(rules, messages)
    markdown = render(rules, matches, today)

    if args.dry_run:
        # Subjects carry emoji; a cp1252 Windows console must not crash on them.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(markdown)
        print(
            f"\n[dry-run] {len(messages)} scanned, {len(matches)} matched; nothing written, nothing posted",
            file=sys.stderr,
        )
        return EXIT_OK

    out = args.out or Path(f"digest-{today.isoformat()}.md")
    out.write_text(markdown, encoding="utf-8")
    print(f"{len(messages)} scanned, {len(matches)} matched -> {out}", file=sys.stderr)

    if args.webhook:
        url = os.environ.get("WEBHOOK_URL")
        if not url:
            return _fail("config", "--webhook given but WEBHOOK_URL is not set (hint: export WEBHOOK_URL)", EXIT_CONFIG)
        try:
            status = post_webhook(url, markdown, fmt=webhook_format)
        except HTTPError as exc:
            # Only the status is reported: the URL is a secret.
            if exc.code in (401, 403):
                return _fail("auth", f"webhook failed: HTTP {exc.code} (hint: the webhook URL was revoked?)", EXIT_AUTH)
            return _fail("network", f"webhook failed: HTTP {exc.code} (hint: retry later)", EXIT_NETWORK)
        except ValueError as exc:
            return _fail("config", f"webhook failed: {exc} (hint: check WEBHOOK_URL and WEBHOOK_FORMAT)", EXIT_CONFIG)
        except URLError as exc:
            if isinstance(exc.reason, OSError):
                return _fail("network", f"webhook failed: {exc.reason} (hint: check the network)", EXIT_NETWORK)
            # Kept from before exit codes were split: a URLError without an OS-level cause exits 1.
            return _fail("webhook", f"webhook failed: {exc.reason}", 1)
        except OSError as exc:
            return _fail("network", f"webhook failed: {exc} (hint: check the network)", EXIT_NETWORK)
        print(f"webhook: HTTP {status}", file=sys.stderr)

    if args.state:
        try:
            save_state(args.state, seen | keys)
        except OSError as exc:
            return _fail("config", f"cannot write state file {args.state}: {exc}", EXIT_CONFIG)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

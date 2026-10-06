"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from urllib.error import URLError

from . import __version__
from .digest import WEBHOOK_FORMATS, post_webhook, render
from .rules import RuleError, apply_rules, load_rules
from .sources import SourceError, fetch_imap, read_eml_dir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mail-rule-digest",
        description="Filter an IMAP mailbox with a TOML rule file and write a Markdown digest.",
    )
    p.add_argument("--rules", type=Path, required=True, help="path to the TOML rule file")
    p.add_argument("--eml-dir", type=Path, help="read .eml files from a folder instead of IMAP")
    p.add_argument("--days", type=int, default=1, help="look back this many days (IMAP only, default 1)")
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
        print(
            f"error: unknown WEBHOOK_FORMAT {webhook_format!r} (choose from {', '.join(WEBHOOK_FORMATS)})",
            file=sys.stderr,
        )
        return 2
    try:
        rules = load_rules(args.rules)
        messages = read_eml_dir(args.eml_dir) if args.eml_dir else fetch_imap(args.days, args.limit)
    except (RuleError, SourceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

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
        return 0

    out = args.out or Path(f"digest-{today.isoformat()}.md")
    out.write_text(markdown, encoding="utf-8")
    print(f"{len(messages)} scanned, {len(matches)} matched -> {out}", file=sys.stderr)

    if args.webhook:
        url = os.environ.get("WEBHOOK_URL")
        if not url:
            print("error: --webhook given but WEBHOOK_URL is not set", file=sys.stderr)
            return 2
        try:
            status = post_webhook(url, markdown, fmt=webhook_format)
        except (URLError, ValueError, OSError) as exc:
            print(f"error: webhook failed: {exc}", file=sys.stderr)
            return 1
        print(f"webhook: HTTP {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

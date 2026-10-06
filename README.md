# mail-rule-digest

[![CI](https://github.com/mahirhir/mail-rule-digest/actions/workflows/ci.yml/badge.svg)](https://github.com/mahirhir/mail-rule-digest/actions/workflows/ci.yml)

Read an IMAP mailbox, keep only the messages that match a small TOML rule file, and write one Markdown digest per day. Optionally post the digest to a Discord or Slack webhook.

- No runtime dependencies: Python 3.12 standard library only (`imaplib`, `email`, `tomllib`, `urllib`).
- Read-only: the folder is opened with `EXAMINE` and messages are fetched with `BODY.PEEK[]`, so nothing is marked as read, moved or deleted.
- Credentials (password or OAuth2 token) come only from environment variables and are never written anywhere.
- `--dry-run` prints the digest and touches nothing else.

## Install

```sh
pip install git+https://github.com/mahirhir/mail-rule-digest
```

Or from a clone: `pip install -e ".[dev]"` (adds pytest and ruff).

## Quick start

```sh
export IMAP_HOST=imap.example.com
export IMAP_USER=you@example.com
export IMAP_PASSWORD='an app password'   # see Security notes
# or, for Gmail / Microsoft 365: IMAP_ACCESS_TOKEN / IMAP_TOKEN_COMMAND (see OAuth2 below)
# optional: IMAP_PORT (default 993), IMAP_FOLDER (default INBOX), WEBHOOK_URL

mail-rule-digest --rules rules.toml --dry-run          # look first
mail-rule-digest --rules rules.toml                    # writes digest-YYYY-MM-DD.md
mail-rule-digest --rules rules.toml --webhook          # and posts it to $WEBHOOK_URL
```

Try it without a mailbox, on the test messages in this repo:

```sh
mail-rule-digest --rules examples/rules.toml --eml-dir tests/fixtures --dry-run
```

| Option | Meaning |
| --- | --- |
| `--rules PATH` | TOML rule file (required) |
| `--eml-dir DIR` | read `*.eml` files from a folder instead of IMAP |
| `--days N` | IMAP look-back window in days, default 1 (today only) |
| `--limit N` | fetch at most the N most recent messages, default 500 |
| `--out PATH` | digest file, default `digest-YYYY-MM-DD.md` |
| `--webhook` | also POST the digest to `$WEBHOOK_URL` (https only) |
| `--dry-run` | print to stdout; write no file, post nothing |

Exit codes: `0` ok, `1` webhook failed, `2` bad rules / configuration / mailbox error.

## OAuth2 (XOAUTH2) for Gmail and Microsoft 365

Many Gmail and Microsoft 365 accounts no longer accept passwords over IMAP. Give the tool a short-lived OAuth2 access token instead; it sends it with the SASL `XOAUTH2` mechanism. Stdlib only, no client secret ships with this project: you register the OAuth client on your side and get the token yourself.

Set one of these (if either is set it is used instead of `IMAP_PASSWORD`):

- `IMAP_ACCESS_TOKEN`: the access token itself.
- `IMAP_TOKEN_COMMAND`: a command whose stdout is the access token, run without a shell (split like a POSIX shell line) with a 30 second timeout. Use this with a CLI that already refreshes tokens for you. A non-zero exit, a timeout or empty output stops the run with a message that never contains the token or the command's output.

```sh
export IMAP_HOST=imap.gmail.com IMAP_USER=you@gmail.com
export IMAP_TOKEN_COMMAND='my-oauth-cli print-access-token you@gmail.com'
mail-rule-digest --rules rules.toml --dry-run
```

Access tokens expire (about an hour); a token command is the practical choice for scheduled runs.

What you must do on your side:

- Gmail (`imap.gmail.com`): create a Google Cloud project, enable the Gmail API, configure the OAuth consent screen, create an OAuth client, and obtain a token with scope `https://mail.google.com/`. Gmail IMAP needs that full-mail scope. An app in "Testing" status issues refresh tokens that expire after 7 days. Workspace admins can restrict third-party apps.
- Microsoft 365 / Outlook.com (`outlook.office365.com`): register an app in Microsoft Entra ID, add the delegated permission `IMAP.AccessAsUser.All` (scope `https://outlook.office365.com/IMAP.AccessAsUser.All`, plus `offline_access` for refresh), and obtain a token for it. Your tenant admin may need to consent and must have IMAP enabled for the mailbox. `IMAP_USER` must be the mailbox address.

Not verified against live Gmail or Microsoft servers by this project's tests (the tests use a fake server); if a provider rejects the token, the error is shown as `IMAP error: ...` without the token.

## Rule file

```toml
[[rule]]
name = "Invoices over $100"
from = ["billing@acme.example", "*@invoices.example"]   # glob, case-insensitive
subject = '(?i)\binvoice\b'                           # Python regex, searched
body_any = ["due", "overdue"]                          # case-insensitive substring

  [rule.numbers.amount]
  # The named group must have the same name as the field.
  pattern = 'Total:\s*\$(?P<amount>[\d,]+(?:\.\d+)?)'
  min = 100

[[rule]]
name = "Disk alerts at 90% or more"
from = "alerts@monitor.example"

  [rule.numbers.disk]
  pattern = '(?i)disk usage:?\s*(?P<disk>\d+)%'
  min = 90
  max = 100

[[rule]]
name = "Parcels out for delivery"
body_all = ["tracking number", "out for delivery"]
```

| Key | Type | Matches when |
| --- | --- | --- |
| `name` | string | required, used as the digest heading |
| `from` | string or list | the sender address matches any glob |
| `subject` | regex | the regex is found in the subject |
| `body_any` | string or list | the body contains at least one keyword |
| `body_all` | string or list | the body contains every keyword |
| `numbers.<field>` | table | `pattern` (with `(?P<field>...)`) is found in subject + body, the value parses as a number (`,` and `_` ignored) and lies within the inclusive `min` / `max` |

All conditions of one rule must hold. A message can match several rules. A rule with no condition, an unknown key, a bad regex or a pattern without its named group is rejected at load time, before any mail is fetched. Use single-quoted TOML strings for regexes so backslashes stay literal.

## Sample digest

This is the real output of the quick-start command above on `tests/fixtures`:

```markdown
# Mail digest 2026-10-06

3 matching message(s) across 3 rule(s).

## Invoices over $100 (1)

- **Invoice INV-1042 is ready** - billing@acme.example, 2026-10-05 09:15 - amount: 1,240.50

## Disk alerts at 90% or more (1)

- **\[ALERT\] disk usage 93% on web-1 @everyone** - alerts@monitor.example, 2026-10-05 12:05 - disk: 93

## Parcels out for delivery (1)

- **Your parcel is out for delivery 📦** - noreply@ship.example, 2026-10-05 11:45
```

When a rule with numeric fields matches more than one message, a `_total <field>: ..._` line is added.

## Running it daily

cron, at 18:00:

```cron
0 18 * * * cd /path/to/digests && . ./mail.env && mail-rule-digest --rules rules.toml --webhook
```

On Windows use Task Scheduler with the same command; keep the variables in the task's environment, not in the rule file.

## Webhooks

- Host `discord.com` / `discordapp.com`: sends `{"content": ..., "allowed_mentions": {"parse": []}}`, cut to Discord's 2000-character limit. Mentions are disabled so a subject containing `@everyone` cannot ping the channel.
- Anything else: sends `{"text": ...}` (Slack incoming webhooks, Mattermost, and most chat bridges accept this).

## Limitations

- IMAP over TLS (port 993) only, authenticated by password or by an OAuth2 access token you supply (XOAUTH2). The tool does not run the OAuth consent flow and does not refresh tokens. No STARTTLS, no POP3.
- `SINCE` in IMAP has day granularity and uses the server's date, so `--days 1` means "since 00:00 today" on the server.
- HTML-only messages are reduced to text with a simple tag strip; good for keywords and numbers, not for layout.
- Numbers are parsed with `.` as the decimal point; `1.234,56` style amounts need a pattern that captures them differently.
- Rules are TOML, not YAML, to stay inside the standard library.
- No state between runs: running twice on the same day produces the same digest again.

## Security notes

- Credentials are read from `IMAP_*` environment variables only (`IMAP_PASSWORD`, `IMAP_ACCESS_TOKEN`, `IMAP_TOKEN_COMMAND`). They are not accepted as command-line flags (which leak into shell history and process lists) and are never logged or written to the digest.
- Prefer an OAuth token, a provider app password or a dedicated read-only account over your main password.
- The mailbox is opened read-only. The tool never sends, deletes, moves or flags mail.
- The digest contains subjects and sender addresses. Treat the output file and the webhook channel as being as private as the mailbox.
- Webhook URLs are secrets too: anyone holding one can post to your channel. Only `https://` URLs are accepted.
- TLS certificates and host names are verified (`ssl.create_default_context()`; note that `imaplib` on its own does not verify).

## Development

```sh
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

Tests use the `.eml` files in `tests/fixtures` and a fake IMAP server; they make no network calls.

## License

MIT

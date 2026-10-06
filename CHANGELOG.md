# Changelog

## Unreleased

- Distinct exit codes and clearer errors, printed as `error: <kind>: <detail>`: `2` bad configuration (rules, `IMAP_*` env, `--eml-dir`, state file, webhook URL/format), `3` authentication failure (IMAP login, token command, webhook HTTP 401/403), `4` network failure (IMAP connect/session, webhook unreachable or other HTTP error). Previously a webhook failure exited `1` and a mailbox failure exited `2`. New `ConfigError`, `AuthError` and `NetworkError` (subclasses of `SourceError`). Secrets are never printed.
- README: new Scheduling section (Task Scheduler, systemd, cron) and an Exit codes table.

- Added `--since YYYY-MM-DD` (date window for IMAP and `--eml-dir`) and `--state PATH` (JSON file of reported messages, so repeated scheduled runs never report the same message twice). A corrupt state file exits with code 2.

- Added selectable webhook formats: `--webhook-format {auto,discord,slack,teams}` or `WEBHOOK_FORMAT`. New `slack` (`text` + `mrkdwn`) and `teams` (Workflows Adaptive Card) formats; `auto` also recognises Teams/Workflows hosts and is otherwise unchanged.

## 0.2.0

- Added OAuth2 (XOAUTH2) authentication via `IMAP_ACCESS_TOKEN` or `IMAP_TOKEN_COMMAND`; password auth is unchanged.

## 0.1.0

- Initial release.

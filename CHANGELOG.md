# Changelog

## Unreleased

- Added selectable webhook formats: `--webhook-format {auto,discord,slack,teams}` or `WEBHOOK_FORMAT`. New `slack` (`text` + `mrkdwn`) and `teams` (Workflows Adaptive Card) formats; `auto` also recognises Teams/Workflows hosts and is otherwise unchanged.

## 0.2.0

- Added OAuth2 (XOAUTH2) authentication via `IMAP_ACCESS_TOKEN` or `IMAP_TOKEN_COMMAND`; password auth is unchanged.

## 0.1.0

- Initial release.

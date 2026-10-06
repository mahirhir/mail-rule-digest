"""Load a TOML rule file and match parsed messages against it."""

from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .message import Message


class RuleError(ValueError):
    """Raised when the rule file is malformed."""


@dataclass(frozen=True)
class NumberField:
    name: str
    pattern: re.Pattern[str]
    min: float | None = None
    max: float | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    senders: tuple[str, ...] = ()
    subject: re.Pattern[str] | None = None
    body_any: tuple[str, ...] = ()
    body_all: tuple[str, ...] = ()
    numbers: tuple[NumberField, ...] = ()


@dataclass(frozen=True)
class Match:
    rule: Rule
    message: Message
    values: dict[str, float] = field(default_factory=dict)


_KNOWN_KEYS = {"name", "from", "subject", "body_any", "body_all", "numbers"}


def _compile(pattern: object, where: str) -> re.Pattern[str]:
    if not isinstance(pattern, str):
        raise RuleError(f"{where}: pattern must be a string")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise RuleError(f"{where}: invalid regex: {exc}") from exc


def _str_list(value: object, where: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise RuleError(f"{where}: expected a string or a list of strings")


def _bound(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RuleError(f"{where}: expected a number")
    return float(value)


def _parse_rule(raw: dict[str, object], index: int) -> Rule:
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise RuleError(f"rule #{index + 1}: 'name' is required")
    where = f"rule '{name}'"
    unknown = set(raw) - _KNOWN_KEYS
    if unknown:
        raise RuleError(f"{where}: unknown keys {sorted(unknown)}")

    numbers: list[NumberField] = []
    raw_numbers = raw.get("numbers", {})
    if not isinstance(raw_numbers, dict):
        raise RuleError(f"{where}: 'numbers' must be a table")
    for field_name, spec in raw_numbers.items():
        fwhere = f"{where}.numbers.{field_name}"
        if not isinstance(spec, dict):
            raise RuleError(f"{fwhere}: must be a table with 'pattern'")
        pattern = _compile(spec.get("pattern"), fwhere)
        if field_name not in pattern.groupindex:
            raise RuleError(f"{fwhere}: pattern needs a named group (?P<{field_name}>...)")
        numbers.append(
            NumberField(
                name=field_name,
                pattern=pattern,
                min=_bound(spec.get("min"), f"{fwhere}.min"),
                max=_bound(spec.get("max"), f"{fwhere}.max"),
            )
        )

    subject = raw.get("subject")
    rule = Rule(
        name=name,
        senders=tuple(s.lower() for s in _str_list(raw.get("from", []), f"{where}.from")),
        subject=_compile(subject, f"{where}.subject") if subject is not None else None,
        body_any=tuple(k.lower() for k in _str_list(raw.get("body_any", []), f"{where}.body_any")),
        body_all=tuple(k.lower() for k in _str_list(raw.get("body_all", []), f"{where}.body_all")),
        numbers=tuple(numbers),
    )
    if not (rule.senders or rule.subject or rule.body_any or rule.body_all or rule.numbers):
        raise RuleError(f"{where}: has no conditions and would match every message")
    return rule


def load_rules(path: Path) -> list[Rule]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RuleError(f"{path}: {exc}") from exc
    raw_rules = data.get("rule")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RuleError(f"{path}: define at least one [[rule]] table")
    return [_parse_rule(r, i) for i, r in enumerate(raw_rules)]


def _to_number(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("_", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def match(rule: Rule, msg: Message) -> Match | None:
    """Return a Match when every condition of the rule holds, else None."""
    if rule.senders and not any(fnmatch.fnmatchcase(msg.sender_addr, p) for p in rule.senders):
        return None
    if rule.subject and not rule.subject.search(msg.subject):
        return None
    body = msg.body.lower()
    if rule.body_any and not any(k in body for k in rule.body_any):
        return None
    if rule.body_all and not all(k in body for k in rule.body_all):
        return None

    values: dict[str, float] = {}
    haystack = f"{msg.subject}\n{msg.body}"
    for nf in rule.numbers:
        m = nf.pattern.search(haystack)
        value = _to_number(m.group(nf.name)) if m and m.group(nf.name) else None
        if value is None:
            return None
        if nf.min is not None and value < nf.min:
            return None
        if nf.max is not None and value > nf.max:
            return None
        values[nf.name] = value
    return Match(rule=rule, message=msg, values=values)


def apply_rules(rules: list[Rule], messages: list[Message]) -> list[Match]:
    return [m for msg in messages for rule in rules if (m := match(rule, msg))]

"""Validate the example scheduler files under examples/schedule/."""

import configparser
import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from mail_rule_digest import cli

SCHEDULE_DIR = Path(__file__).resolve().parent.parent / "examples" / "schedule"
TASK_XML = SCHEDULE_DIR / "mail-rule-digest-task.xml"
SERVICE = SCHEDULE_DIR / "mail-rule-digest.service"
TIMER = SCHEDULE_DIR / "mail-rule-digest.timer"
CRON = SCHEDULE_DIR / "crontab.example"
ALL_FILES = [TASK_XML, SERVICE, TIMER, CRON]

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
OPTIONAL_FLAGS = {"--state", "--since"}  # added by later tasks
CRON_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]


def _ini(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str  # keep key case
    cp.read(path, encoding="utf-8")
    return cp


def _cron_line() -> str:
    lines = [ln for ln in CRON.read_text(encoding="ascii").splitlines() if ln.strip() and not ln.startswith("#")]
    assert len(lines) == 1
    return lines[0]


def _task_exec() -> ET.Element:
    root = ET.parse(TASK_XML).getroot()
    node = root.find(f"{{{TASK_NS}}}Actions/{{{TASK_NS}}}Exec")
    assert node is not None
    return node


def _check_flags(args: list[str]) -> None:
    parser = cli.build_parser()
    for tok in args:
        if not tok.startswith("--"):
            continue
        flag = tok.split("=", 1)[0]
        if flag in OPTIONAL_FLAGS and flag not in parser._option_string_actions:
            continue
        assert flag in parser._option_string_actions, f"unknown flag {flag}"


@pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: p.name)
def test_files_exist_ascii_no_secrets(path):
    data = path.read_bytes()
    data.decode("ascii")
    text = data.decode("ascii")
    for m in re.finditer(r"(?i)\b(password|token)=(\S+)", text):
        assert re.match(r"[<$%{]|changeme|placeholder", m.group(2)), m.group(0)


def test_task_xml():
    root = ET.parse(TASK_XML).getroot()
    assert root.tag == f"{{{TASK_NS}}}Task"
    assert root.find(f"{{{TASK_NS}}}Triggers/{{{TASK_NS}}}CalendarTrigger/{{{TASK_NS}}}ScheduleByDay") is not None
    ex = _task_exec()
    assert ex.findtext(f"{{{TASK_NS}}}Command")
    assert ex.findtext(f"{{{TASK_NS}}}WorkingDirectory")
    args = ex.findtext(f"{{{TASK_NS}}}Arguments")
    assert "--rules" in args
    assert "mail_rule_digest" in args
    for el in root.iter():
        assert "password" not in el.tag.lower()
        assert "password" not in (el.text or "").lower()
    _check_flags(shlex.split(args, posix=False))


def test_service_unit():
    cp = _ini(SERVICE)
    assert {"Unit", "Service"} <= set(cp.sections())
    svc = cp["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["EnvironmentFile"] == "%h/.config/mail-rule-digest/env"
    exec_start = svc["ExecStart"]
    assert "--rules" in exec_start
    _check_flags(shlex.split(exec_start))


def test_timer_unit():
    cp = _ini(TIMER)
    assert {"Unit", "Timer", "Install"} <= set(cp.sections())
    assert cp["Timer"]["OnCalendar"]
    assert cp["Timer"]["Persistent"] == "true"
    assert cp["Timer"]["Unit"] == SERVICE.name
    assert cp["Install"]["WantedBy"] == "timers.target"


def _field_ok(field: str, lo: int, hi: int) -> bool:
    if field == "*":
        return True
    if m := re.fullmatch(r"\*/(\d+)", field):
        return 1 <= int(m.group(1)) <= hi
    for part in field.split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            return False
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if not (lo <= a <= b <= hi):
            return False
    return True


def test_cron_line():
    line = _cron_line()
    fields = line.split(None, 5)
    assert len(fields) == 6
    for field, (lo, hi) in zip(fields[:5], CRON_RANGES, strict=True):
        assert _field_ok(field, lo, hi), field
    command = fields[5]
    assert "env" in command  # secrets come from an env file
    assert "--rules" in command
    _check_flags(shlex.split(command))


def test_cron_field_checker():
    assert _field_ok("*/15", 0, 59)
    assert not _field_ok("61", 0, 59)
    assert not _field_ok("x", 0, 59)

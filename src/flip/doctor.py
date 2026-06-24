"""Deck health diagnostics and migration advice."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import engine, store
from .importers import validate_tiku
from .repair import build_repair_plan


UUID_RE = re.compile(r"^q-[0-9a-f]{12}$")
LEGACY_POSITIONAL_RE = re.compile(r"^[a-z0-9-]+-\d+-\d{3}$")
HEADER_WIDTH = 40


@dataclass
class DoctorReport:
    slug: str
    tiku_errors: list[str] = field(default_factory=list)
    question_count: int = 0
    chapter_count: int = 0
    missing_ids: int = 0
    uuid_ids: int = 0
    legacy_positional_ids: int = 0
    other_ids: int = 0
    duplicate_ids: int = 0
    wrong_files: int = 0
    wrong_records: int = 0
    wrong_resolvable: int = 0
    wrong_stale: int = 0

    @property
    def needs_id_migration(self):
        return self.missing_ids > 0 or self.legacy_positional_ids > 0

    @property
    def needs_repair(self):
        return bool(self.tiku_errors) or self.wrong_stale > 0


def build_doctor_report(deck) -> DoctorReport:
    data = store.load_tiku(deck)
    report = DoctorReport(slug=deck.slug, tiku_errors=validate_tiku(data))
    records = list(engine.iter_question_records(data))
    report.question_count = len(records)
    report.chapter_count = len({chapter for chapter, _ in records})

    seen_ids = {}
    for chapter, q in records:
        qid = engine.question_id(q)
        if not qid:
            report.missing_ids += 1
            continue
        if qid in seen_ids:
            report.duplicate_ids += 1
        else:
            seen_ids[qid] = chapter
        if UUID_RE.fullmatch(qid):
            report.uuid_ids += 1
        elif LEGACY_POSITIONAL_RE.fullmatch(qid):
            report.legacy_positional_ids += 1
        else:
            report.other_ids += 1

    # Repair planning already knows how to resolve wrong-index records. If tiku
    # is invalid, keep doctor useful by reporting tiku errors and skipping the
    # index scan rather than failing the whole command.
    if not report.tiku_errors:
        repair = build_repair_plan(deck)
        report.wrong_files = repair.wrong.files
        report.wrong_records = repair.wrong.records
        report.wrong_resolvable = repair.wrong.resolvable
        report.wrong_stale = repair.wrong.stale
    return report


def format_report(report: DoctorReport) -> list[str]:
    lines = [
        _center_header(f"doctor: {report.slug}"),
        "",
        "Doctor Results:",
        f"1. tiku: questions={report.question_count}, chapters={report.chapter_count}, "
        f"errors={len(report.tiku_errors)}",
        f"2. ids: missing ids={report.missing_ids}, uuid={report.uuid_ids}, "
        f"legacy positional={report.legacy_positional_ids}, other={report.other_ids}, "
        f"duplicates={report.duplicate_ids}",
        f"3. wrong: files={report.wrong_files}, records={report.wrong_records}, "
        f"resolvable={report.wrong_resolvable}, stale={report.wrong_stale}",
    ]
    if report.tiku_errors:
        lines.append("")
        lines.append("Tiku Errors:")
        for index, error in enumerate(report.tiku_errors[:10], start=1):
            lines.append(f"{index}. {error}")
    lines.append("---")
    fixes = fix_commands(report)
    if fixes:
        lines.append("fix commands:")
        lines.extend(f"- {cmd}" for cmd in fixes)
    else:
        lines.append("fix commands: none")
    return lines


def fix_commands(report: DoctorReport) -> list[str]:
    commands = []
    if report.needs_id_migration:
        commands.append(f"flip deck migrate {report.slug} --ids")
    if report.needs_repair:
        commands.append(f"flip deck repair {report.slug}")
    return commands


def _center_header(title: str, width: int = HEADER_WIDTH) -> str:
    label = f" {title} "
    if len(label) >= width - 2:
        return f"---- {title} ----"
    left = (width - len(label)) // 2
    right = width - len(label) - left
    return f"{'-' * left}{label}{'-' * right}"


def migration_warning(deck) -> str:
    report = build_doctor_report(deck)
    if not report.needs_id_migration:
        return ""
    problems = []
    if report.missing_ids:
        problems.append(f"{report.missing_ids} 道题缺少稳定 id")
    if report.legacy_positional_ids:
        problems.append(f"{report.legacy_positional_ids} 道题使用旧位置 id")
    return (
        f"当前 deck 有{'，'.join(problems)}；"
        "内容编辑可能无法可靠持久化。\n"
        f"建议运行：flip deck migrate {deck.slug} --ids"
    )

"""Lessons-per-week management.

LPW comes from a grade-level template (data/metadata/grade_{level}.json).
Per-student manual overrides live in the SubjectMeta DB and always win.
"""

import json

from src.config import METADATA_DIR
from src.db import SessionLocal, SubjectMeta


def _read_template(grade_level: int) -> dict:
    path = METADATA_DIR / f"grade_{grade_level}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_manual_lpw(
    student_id: str,
    year: int,
    subject: str,
    lessons_per_week: int,
    include_in_gpa: bool = True,
) -> None:
    with SessionLocal() as db:
        row = db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
            SubjectMeta.subject_name == subject,
        ).first()
        if row:
            row.lessons_per_week = lessons_per_week
            row.include_in_gpa = include_in_gpa
            row.is_manual_override = True
        else:
            db.add(SubjectMeta(
                student_username=student_id,
                academic_year=year,
                subject_name=subject,
                lessons_per_week=lessons_per_week,
                include_in_gpa=include_in_gpa,
                is_manual_override=True,
            ))
        db.commit()


def delete_manual_lpw(student_id: str, year: int, subject: str) -> None:
    with SessionLocal() as db:
        db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
            SubjectMeta.subject_name == subject,
        ).delete()
        db.commit()


def get_lpw_dict(student_id: str, year: int, grade_level: int) -> dict[str, dict]:
    """Return {subject: {lessons_per_week, necessary}} — template + manual overrides."""
    result: dict[str, dict] = {
        subject: {
            "lessons_per_week": int(meta.get("lessons_per_week", 0)),
            "necessary": bool(meta.get("necessary", True)),
        }
        for subject, meta in _read_template(grade_level).items()
    }

    with SessionLocal() as db:
        rows = db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
        ).all()

    for r in rows:
        result[r.subject_name] = {
            "lessons_per_week": r.lessons_per_week,
            "necessary": r.include_in_gpa,
        }

    return result

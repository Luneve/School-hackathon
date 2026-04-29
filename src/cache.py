"""Database-backed storage for grades, manual predictions, external GPAs, sync logs."""

import json
from datetime import datetime

from src.models import YearData, Semester, Grade
from src.db import (
    SessionLocal,
    GradeEntry,
    Student,
    ExternalYearGPA,
    SyncLog,
)


def is_student_data_cached(student_id: str, year: int) -> bool:
    with SessionLocal() as db:
        return db.query(GradeEntry).filter(
            GradeEntry.student_username == student_id,
            GradeEntry.academic_year == year,
        ).count() > 0


def load_student_cache(student_id: str, year: int) -> YearData:
    with SessionLocal() as db:
        rows = db.query(GradeEntry).filter(
            GradeEntry.student_username == student_id,
            GradeEntry.academic_year == year,
        ).all()

    sem1 = Semester(available=False)
    sem2 = Semester(available=False)

    for r in rows:
        grade = Grade(
            title=r.title,
            category_id=r.category_id,
            importance=r.importance,
            percent=r.percent,
            is_manual_override=r.is_manual_override,
            class_avg_percent=r.class_avg_percent or 0.0,
        )
        bucket = sem1 if r.semester == 1 else sem2
        bucket.available = True
        bucket.grades.setdefault(r.subject_name, []).append(grade)

    return YearData(semesters={"semester-1": sem1, "semester-2": sem2})


def save_student_cache(student_id: str, year: int, data: YearData) -> None:
    """Replace EduPage rows for `year` with `data`. Manual overrides are preserved.
    Writes a SyncLog entry summarising the diff per semester.
    """
    with SessionLocal() as db:
        student = db.query(Student).filter(Student.username == student_id).first()
        if not student:
            student = Student(username=student_id, graduation_year=0)
            db.add(student)
        student.last_synced_at = datetime.utcnow()

        for sem_name, sem_data in data.semesters.items():
            if not sem_data.available:
                continue
            sem_num = 1 if sem_name == "semester-1" else 2

            old_rows = db.query(GradeEntry).filter(
                GradeEntry.student_username == student_id,
                GradeEntry.academic_year == year,
                GradeEntry.semester == sem_num,
                GradeEntry.is_manual_override == False,  # noqa: E712
            ).all()
            old = {(r.subject_name, r.title): r.percent for r in old_rows}

            new: dict[tuple, float] = {}
            for subject, grades in sem_data.grades.items():
                for g in grades:
                    if not g.is_manual_override:
                        new[(subject, g.title)] = g.percent

            db.query(GradeEntry).filter(
                GradeEntry.student_username == student_id,
                GradeEntry.academic_year == year,
                GradeEntry.semester == sem_num,
                GradeEntry.is_manual_override == False,  # noqa: E712
            ).delete()

            for subject, grades in sem_data.grades.items():
                for g in grades:
                    if g.is_manual_override:
                        continue
                    db.add(GradeEntry(
                        student_username=student_id,
                        academic_year=year,
                        semester=sem_num,
                        subject_name=subject,
                        title=g.title,
                        category_id=g.category_id,
                        importance=g.importance,
                        percent=g.percent,
                        class_avg_percent=g.class_avg_percent,
                        is_manual_override=False,
                    ))

            added = [f"{s} · {t}" for (s, t) in new if (s, t) not in old]
            removed = [f"{s} · {t}" for (s, t) in old if (s, t) not in new]
            updated = [
                f"{s} · {t}: {old[(s, t)]}% → {new[(s, t)]}%"
                for (s, t) in new
                if (s, t) in old and old[(s, t)] != new[(s, t)]
            ]
            if added or removed or updated:
                db.add(SyncLog(
                    student_username=student_id,
                    synced_at=datetime.utcnow(),
                    academic_year=year,
                    semester=sem_num,
                    changes=json.dumps({"added": added, "updated": updated, "removed": removed}),
                ))

        db.commit()


def get_last_synced_at(student_id: str) -> datetime | None:
    with SessionLocal() as db:
        student = db.query(Student).filter(Student.username == student_id).first()
        return student.last_synced_at if student else None


def add_manual_grade_to_db(
    student_id: str, year: int, semester: int, subject: str, g: Grade
) -> None:
    with SessionLocal() as db:
        db.add(GradeEntry(
            student_username=student_id,
            academic_year=year,
            semester=semester,
            subject_name=subject,
            title=g.title,
            category_id=g.category_id,
            importance=g.importance,
            percent=g.percent,
            class_avg_percent=0.0,
            is_manual_override=True,
        ))
        db.commit()


def get_manual_grades(student_id: str, year: int) -> list[dict]:
    with SessionLocal() as db:
        rows = db.query(GradeEntry).filter(
            GradeEntry.student_username == student_id,
            GradeEntry.academic_year == year,
            GradeEntry.is_manual_override == True,  # noqa: E712
        ).order_by(GradeEntry.semester, GradeEntry.subject_name, GradeEntry.id).all()

        return [
            {
                "id": r.id,
                "semester": r.semester,
                "subject": r.subject_name,
                "title": r.title,
                "category_id": r.category_id,
                "importance": r.importance,
                "percent": r.percent,
            }
            for r in rows
        ]


def delete_manual_grade(grade_id: int, student_id: str) -> bool:
    with SessionLocal() as db:
        entry = db.query(GradeEntry).filter(
            GradeEntry.id == grade_id,
            GradeEntry.student_username == student_id,
            GradeEntry.is_manual_override == True,  # noqa: E712
        ).first()
        if not entry:
            return False
        db.delete(entry)
        db.commit()
        return True


def get_external_year_gpas(student_id: str) -> list[dict]:
    with SessionLocal() as db:
        rows = db.query(ExternalYearGPA).filter(
            ExternalYearGPA.student_username == student_id,
        ).order_by(ExternalYearGPA.academic_year).all()
        return [
            {"academic_year": r.academic_year, "gpa_points": r.gpa_points, "label": r.label}
            for r in rows
        ]


def upsert_external_year_gpa(
    student_id: str, academic_year: int, gpa_points: float, label: str = ""
) -> None:
    with SessionLocal() as db:
        existing = db.query(ExternalYearGPA).filter(
            ExternalYearGPA.student_username == student_id,
            ExternalYearGPA.academic_year == academic_year,
        ).first()
        if existing:
            existing.gpa_points = gpa_points
            existing.label = label
            db.commit()
            return

        if not db.query(Student).filter(Student.username == student_id).first():
            db.add(Student(username=student_id, graduation_year=0))
        db.add(ExternalYearGPA(
            student_username=student_id,
            academic_year=academic_year,
            gpa_points=gpa_points,
            label=label,
        ))
        db.commit()


def delete_external_year_gpa(student_id: str, academic_year: int) -> None:
    with SessionLocal() as db:
        db.query(ExternalYearGPA).filter(
            ExternalYearGPA.student_username == student_id,
            ExternalYearGPA.academic_year == academic_year,
        ).delete()
        db.commit()


def get_sync_logs(student_id: str, limit: int = 30) -> list[dict]:
    with SessionLocal() as db:
        rows = db.query(SyncLog).filter(
            SyncLog.student_username == student_id,
        ).order_by(SyncLog.synced_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "synced_at": r.synced_at,
                "academic_year": r.academic_year,
                "semester": r.semester,
                "changes": json.loads(r.changes) if r.changes else {},
            }
            for r in rows
        ]

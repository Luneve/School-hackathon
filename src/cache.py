import json
from datetime import datetime
from src.models import YearData, Semester, Grade
from src.db import (
    SessionLocal,
    GradeEntry,
    Student,
    SubjectMeta,
    ExternalYearGPA,
    SyncLog,
    init_db,
)


# ---------------------------------------------------------------------------
# Grade cache (EduPage ↔ DB)
# ---------------------------------------------------------------------------

def is_student_data_cached(student_id: str, year: int) -> bool:
    with SessionLocal() as db:
        count = db.query(GradeEntry).filter(
            GradeEntry.student_username == student_id,
            GradeEntry.academic_year == year,
        ).count()
        return count > 0


def load_student_cache(student_id: str, year: int) -> YearData:
    with SessionLocal() as db:
        grades = db.query(GradeEntry).filter(
            GradeEntry.student_username == student_id,
            GradeEntry.academic_year == year,
        ).all()

        semester_1 = Semester(available=False)
        semester_2 = Semester(available=False)

        for g in grades:
            model_grade = Grade(
                title=g.title,
                category_id=g.category_id,
                importance=g.importance,
                percent=g.percent,
                is_manual_override=g.is_manual_override,
                class_avg_percent=g.class_avg_percent or 0.0,
            )

            if g.semester == 1:
                semester_1.available = True
                semester_1.grades.setdefault(g.subject_name, []).append(model_grade)
            else:
                semester_2.available = True
                semester_2.grades.setdefault(g.subject_name, []).append(model_grade)

        return YearData(
            semesters={
                "semester-1": semester_1,
                "semester-2": semester_2,
            }
        )


def _snapshot_edupage_grades(db, student_id: str, year: int, semester: int) -> dict:
    """Return {(subject, title): percent} for existing EduPage grades."""
    rows = db.query(GradeEntry).filter(
        GradeEntry.student_username == student_id,
        GradeEntry.academic_year == year,
        GradeEntry.semester == semester,
        GradeEntry.is_manual_override == False,  # noqa: E712
    ).all()
    return {(r.subject_name, r.title): r.percent for r in rows}


def save_student_cache(student_id: str, year: int, data: YearData) -> None:
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

            # Snapshot before delete for diff
            old_snapshot = _snapshot_edupage_grades(db, student_id, year, sem_num)

            # Build new snapshot from incoming data
            new_snapshot: dict[tuple, float] = {}
            entries_to_add = []
            for subject, subject_grades in sem_data.grades.items():
                for g in subject_grades:
                    if not g.is_manual_override:
                        new_snapshot[(subject, g.title)] = g.percent
                        entries_to_add.append((subject, g))

            # Delete old EduPage grades for this semester
            db.query(GradeEntry).filter(
                GradeEntry.student_username == student_id,
                GradeEntry.academic_year == year,
                GradeEntry.semester == sem_num,
                GradeEntry.is_manual_override == False,  # noqa: E712
            ).delete()

            # Insert new grades
            for subject, g in entries_to_add:
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

            # Compute and record diff
            added = [f"{s} · {t}" for (s, t) in new_snapshot if (s, t) not in old_snapshot]
            removed = [f"{s} · {t}" for (s, t) in old_snapshot if (s, t) not in new_snapshot]
            updated = [
                f"{s} · {t}: {old_snapshot[(s,t)]}% → {new_snapshot[(s,t)]}%"
                for (s, t) in new_snapshot
                if (s, t) in old_snapshot and old_snapshot[(s, t)] != new_snapshot[(s, t)]
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
        if not student:
            return None
        return student.last_synced_at


# ---------------------------------------------------------------------------
# Manual grade overrides
# ---------------------------------------------------------------------------

def add_manual_grade_to_db(student_id: str, year: int, semester: int, subject: str, g: Grade) -> None:
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


# ---------------------------------------------------------------------------
# SubjectMeta (lessons per week)
# ---------------------------------------------------------------------------

def _seed_subject_meta_from_json(student_id: str, year: int, grade_level: int) -> None:
    """One-time seed of SubjectMeta from the grade_X.json file if it exists."""
    import json as _json
    from src.config import METADATA_DIR
    path = METADATA_DIR / f"grade_{grade_level}.json"
    if not path.exists():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    with SessionLocal() as db:
        for subject, meta in data.items():
            lpw = int(meta.get("lessons_per_week", 0))
            necessary = bool(meta.get("necessary", True))
            existing = db.query(SubjectMeta).filter(
                SubjectMeta.student_username == student_id,
                SubjectMeta.academic_year == year,
                SubjectMeta.subject_name == subject,
            ).first()
            if not existing:
                db.add(SubjectMeta(
                    student_username=student_id,
                    academic_year=year,
                    subject_name=subject,
                    lessons_per_week=lpw,
                    include_in_gpa=necessary,
                    is_manual_override=False,
                ))
        db.commit()


def get_subject_meta_dict(student_id: str, year: int, grade_level: int | None = None) -> dict:
    """Return {subject: {"lessons_per_week": int, "necessary": bool}} for use by calculator.

    If SubjectMeta is empty for this year and grade_level is provided,
    auto-seeds from the grade_X.json file so GPA works on first login.
    """
    with SessionLocal() as db:
        rows = db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
        ).all()

    if not rows and grade_level is not None:
        _seed_subject_meta_from_json(student_id, year, grade_level)
        with SessionLocal() as db:
            rows = db.query(SubjectMeta).filter(
                SubjectMeta.student_username == student_id,
                SubjectMeta.academic_year == year,
            ).all()

    return {
        r.subject_name: {
            "lessons_per_week": r.lessons_per_week,
            "necessary": r.include_in_gpa,
        }
        for r in rows
    }


def upsert_subject_meta(
    student_id: str,
    year: int,
    subject: str,
    lessons_per_week: int,
    include_in_gpa: bool = True,
    is_manual_override: bool = False,
) -> None:
    with SessionLocal() as db:
        existing = db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
            SubjectMeta.subject_name == subject,
        ).first()

        if existing:
            # Never overwrite a manual override with an auto-imported value
            if existing.is_manual_override and not is_manual_override:
                return
            existing.lessons_per_week = lessons_per_week
            existing.include_in_gpa = include_in_gpa
            existing.is_manual_override = is_manual_override
        else:
            db.add(SubjectMeta(
                student_username=student_id,
                academic_year=year,
                subject_name=subject,
                lessons_per_week=lessons_per_week,
                include_in_gpa=include_in_gpa,
                is_manual_override=is_manual_override,
            ))
        db.commit()


def delete_subject_meta(student_id: str, year: int, subject: str) -> None:
    with SessionLocal() as db:
        db.query(SubjectMeta).filter(
            SubjectMeta.student_username == student_id,
            SubjectMeta.academic_year == year,
            SubjectMeta.subject_name == subject,
        ).delete()
        db.commit()


def bulk_upsert_subject_meta_from_timetable(
    student_id: str, year: int, lessons: dict[str, int]
) -> None:
    """Insert timetable-derived lessons_per_week; skips subjects with manual overrides."""
    with SessionLocal() as db:
        existing = {
            r.subject_name: r
            for r in db.query(SubjectMeta).filter(
                SubjectMeta.student_username == student_id,
                SubjectMeta.academic_year == year,
            ).all()
        }
        for subject, count in lessons.items():
            if subject in existing:
                if existing[subject].is_manual_override:
                    continue  # Preserve manual edits
                existing[subject].lessons_per_week = count
            else:
                db.add(SubjectMeta(
                    student_username=student_id,
                    academic_year=year,
                    subject_name=subject,
                    lessons_per_week=count,
                    include_in_gpa=True,
                    is_manual_override=False,
                ))
        db.commit()


# ---------------------------------------------------------------------------
# ExternalYearGPA (manual entry for years outside school range)
# ---------------------------------------------------------------------------

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
        else:
            # Ensure student row exists
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


# ---------------------------------------------------------------------------
# SyncLog
# ---------------------------------------------------------------------------

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

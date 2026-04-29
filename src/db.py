from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    ForeignKey,
    DateTime,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from src.config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "database.db"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Student(Base):
    __tablename__ = "student"

    username = Column(String, primary_key=True, index=True)
    graduation_year = Column(Integer)
    last_synced_at = Column(DateTime, nullable=True)

    grades = relationship("GradeEntry", back_populates="student")


class GradeEntry(Base):
    __tablename__ = "grade"

    id = Column(Integer, primary_key=True, index=True)
    student_username = Column(String, ForeignKey("student.username"), index=True)
    academic_year = Column(Integer, index=True)
    semester = Column(Integer)  # 1 or 2
    subject_name = Column(String, index=True)
    title = Column(String)
    category_id = Column(String)
    importance = Column(Float)
    percent = Column(Float)
    class_avg_percent = Column(Float, default=0.0)
    is_manual_override = Column(Boolean, default=False)

    student = relationship("Student", back_populates="grades")


class SubjectMeta(Base):
    """Stores lessons-per-week and GPA inclusion flag per subject per year."""
    __tablename__ = "subject_meta"

    id = Column(Integer, primary_key=True, index=True)
    student_username = Column(String, ForeignKey("student.username"), index=True)
    academic_year = Column(Integer, index=True)
    subject_name = Column(String)
    lessons_per_week = Column(Integer, default=0)
    include_in_gpa = Column(Boolean, default=True)
    is_manual_override = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint(
            "student_username", "academic_year", "subject_name",
            name="uq_subject_meta"
        ),
    )


class ExternalYearGPA(Base):
    """Manually entered GPA for years outside the student's grade range."""
    __tablename__ = "external_year_gpa"

    id = Column(Integer, primary_key=True, index=True)
    student_username = Column(String, ForeignKey("student.username"), index=True)
    academic_year = Column(Integer)
    gpa_points = Column(Float)
    label = Column(String, default="")

    __table_args__ = (
        UniqueConstraint(
            "student_username", "academic_year",
            name="uq_external_year"
        ),
    )


class SyncLog(Base):
    """Records what changed on each EduPage sync."""
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, index=True)
    student_username = Column(String, ForeignKey("student.username"), index=True)
    synced_at = Column(DateTime)
    academic_year = Column(Integer)
    semester = Column(Integer)  # 1 or 2
    changes = Column(String)   # JSON: {"added": [...], "updated": [...], "removed": [...]}


def _apply_lightweight_migrations():
    """ALTER existing tables to add columns introduced after initial creation."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    if "student" not in tables:
        return

    student_cols = {c["name"] for c in inspector.get_columns("student")}
    if "last_synced_at" not in student_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE student ADD COLUMN last_synced_at TIMESTAMP"))

    if "grade" in tables:
        grade_cols = {c["name"] for c in inspector.get_columns("grade")}
        if "class_avg_percent" not in grade_cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE grade ADD COLUMN class_avg_percent REAL DEFAULT 0.0"
                ))


def init_db():
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations()

import os
import copy
from pathlib import Path

from src.utils import get_graduation_year, get_grade_range, get_current_year, get_grade_for_year
from src.calculator import calculate_annual_percent, calculate_gpa, calculate_overall_gpa_from_years
from src.cache import (
    load_student_cache,
    save_student_cache,
    is_student_data_cached,
    add_manual_grade_to_db,
    get_subject_meta_dict,
    bulk_upsert_subject_meta_from_timetable,
)
from src.edu_api import login, get_all_semesters_data, get_timetable_lessons_per_week
from src.models import Grade, Semester, YearData
from src.db import init_db


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and not os.getenv(key):
            os.environ[key] = value


def get_credentials() -> tuple[str | None, str | None]:
    return os.getenv("EDUPAGE_USERNAME"), os.getenv("EDUPAGE_PASSWORD")


def ensure_year_cached(session, student_id: str, year: int) -> None:
    """Fetch and cache a year's grades from EduPage if not already cached."""
    if is_student_data_cached(student_id, year):
        return
    if session is None:
        return
    data = get_all_semesters_data(session, year)
    save_student_cache(student_id, year, data)


def update_current_year_grades(session, username: str, sync_timetable: bool = True) -> int:
    """Fetch current year grades (and optionally timetable) from EduPage and persist."""
    student_id = username.split("@")[0]
    current_year = get_current_year()
    data = get_all_semesters_data(session, current_year)
    save_student_cache(student_id, current_year, data)

    if sync_timetable:
        try:
            lessons = get_timetable_lessons_per_week(session, current_year)
            if lessons:
                bulk_upsert_subject_meta_from_timetable(student_id, current_year, lessons)
        except Exception:
            pass  # timetable import is best-effort

    return current_year


def calculate_student_gpa_report(
    session, username: str, year_overrides: dict[int, YearData] | None = None
) -> tuple[dict[int, float], float]:
    """Calculate per-year and overall GPA.

    session may be None if all required years are already cached in the DB.
    Uncached years that cannot be fetched are silently skipped.
    """
    student_id = username.split("@")[0]
    graduation_year = get_graduation_year(username)
    current_year = get_current_year()
    grade_range = get_grade_range(graduation_year, current_year)

    yearly_annual_grades = {}
    yearly_metadata = {}
    yearly_gpas = {}
    year_overrides = year_overrides or {}

    for year in grade_range:
        if year in year_overrides:
            grades = year_overrides[year]
        else:
            ensure_year_cached(session, student_id, year)
            grades = load_student_cache(student_id, year)

        annual_percent = calculate_annual_percent(grades)
        grade_level = get_grade_for_year(graduation_year, year)
        metadata = get_subject_meta_dict(student_id, year, grade_level)

        if not metadata:
            continue  # No subject meta and no JSON fallback — skip this year

        yearly_annual_grades[year] = annual_percent
        yearly_metadata[year] = metadata
        yearly_gpas[year] = calculate_gpa(annual_percent, metadata)

    overall_gpa = calculate_overall_gpa_from_years(yearly_annual_grades, yearly_metadata)
    return yearly_gpas, overall_gpa


def get_full_dashboard_data(
    session,
    username: str,
    year_overrides: dict[int, YearData] | None = None,
) -> dict:
    """Return all data needed to render the multi-year transcript dashboard.

    Returns:
        {
            "years": [
                {
                    "year": int,
                    "grade_level": int,
                    "gpa": float,
                    "letter": str,
                    "is_current": bool,
                    "has_meta": bool,        # False if SubjectMeta is empty
                    "subjects": [
                        {
                            "subject": str,
                            "sem1": float | None,
                            "sem2": float | None,
                            "annual": float | None,
                            "letter": str,
                            "gpa": float,
                            "lessons_per_week": int,
                            "include_in_gpa": bool,
                            "status": "complete" | "in_progress" | "not_started",
                            "at_risk": bool,
                            "breakdown": {FA: float, SA: float, MID: float},
                        }
                    ],
                    "subject_meta": {subject: {lessons_per_week, necessary}},
                }
            ],
            "overall_gpa": float,
            "overall_letter": str,
        }
    """
    from src.calculator import (
        calculate_subject_gpa,
        calculate_final_grade,
        percent_to_scale,
        gpa_to_letter,
    )

    student_id = username.split("@")[0]
    graduation_year = get_graduation_year(username)
    current_year = get_current_year()
    grade_range = get_grade_range(graduation_year, current_year)
    year_overrides = year_overrides or {}

    yearly_annual_grades: dict[int, dict] = {}
    yearly_metadata: dict[int, dict] = {}
    years_out = []

    for year in grade_range:
        if year in year_overrides:
            year_data = year_overrides[year]
        else:
            ensure_year_cached(session, student_id, year)
            year_data = load_student_cache(student_id, year)

        annual_percents = calculate_annual_percent(year_data)
        grade_level = get_grade_for_year(graduation_year, year)
        subject_meta = get_subject_meta_dict(student_id, year, grade_level)
        year_gpa = calculate_gpa(annual_percents, subject_meta) if subject_meta else 0.0

        yearly_annual_grades[year] = annual_percents
        yearly_metadata[year] = subject_meta

        # Collect all grades per subject across both semesters
        all_grades_per_subject: dict[str, list] = {}
        for sem in year_data.semesters.values():
            if not sem or not sem.available:
                continue
            for subj, grades in sem.grades.items():
                all_grades_per_subject.setdefault(subj, []).extend(grades)

        subjects_out = []
        all_subjects = sorted(set(list(annual_percents.keys()) + list(all_grades_per_subject.keys())))
        for subject in all_subjects:
            meta = subject_meta.get(subject, {})
            ann = annual_percents.get(subject, {})
            sem1 = ann.get("sem1")
            sem2 = ann.get("sem2")
            annual = ann.get("annual")

            if annual is None:
                status = "not_started"
                letter, gpa_pts = "—", 0.0
            else:
                letter, gpa_pts = percent_to_scale(annual)
                if sem1 is not None and sem2 is not None:
                    status = "complete"
                else:
                    status = "in_progress"

            breakdown = calculate_subject_gpa(all_grades_per_subject.get(subject, []))
            at_risk = (status != "not_started") and gpa_pts < 3.0 and meta.get("necessary", True)

            subjects_out.append({
                "subject": subject,
                "sem1": sem1,
                "sem2": sem2,
                "annual": annual,
                "letter": letter,
                "gpa": gpa_pts,
                "lessons_per_week": meta.get("lessons_per_week", 0),
                "include_in_gpa": meta.get("necessary", True),
                "status": status,
                "at_risk": at_risk,
                "breakdown": breakdown,
            })

        years_out.append({
            "year": year,
            "grade_level": grade_level,
            "gpa": year_gpa,
            "letter": gpa_to_letter(year_gpa),
            "is_current": year == current_year,
            "has_meta": bool(subject_meta),
            "subjects": subjects_out,
            "subject_meta": subject_meta,
        })

    overall_gpa = calculate_overall_gpa_from_years(yearly_annual_grades, yearly_metadata)
    return {
        "years": years_out,
        "overall_gpa": overall_gpa,
        "overall_letter": gpa_to_letter(overall_gpa),
    }


def print_gpa_report(yearly_gpas: dict[int, float], overall_gpa: float) -> None:
    if not yearly_gpas:
        print("No yearly GPA could be calculated.")
        return

    for year, gpa in sorted(yearly_gpas.items()):
        print(f"{year} GPA: {gpa}")

    print(f"Overall GPA: {overall_gpa}")


def run_gpa_flow() -> int:
    username, password = get_credentials()
    if not username or not password:
        print("Missing credentials. Set EDUPAGE_USERNAME and EDUPAGE_PASSWORD.")
        return 1

    session = login(username, password)
    if session is None:
        print("Failed to login to EduPage.")
        return 1

    yearly_gpas, overall_gpa = calculate_student_gpa_report(session, username)
    print_gpa_report(yearly_gpas, overall_gpa)
    return 0


def run_update_current_year_flow() -> int:
    username, password = get_credentials()
    if not username or not password:
        print("Missing credentials. Set EDUPAGE_USERNAME and EDUPAGE_PASSWORD.")
        return 1

    session = login(username, password)
    if session is None:
        print("Failed to login to EduPage.")
        return 1

    updated_year = update_current_year_grades(session, username)
    print(f"Updated cached grades for current year: {updated_year}")
    return 0


def _ask_choice(prompt: str, allowed: list[str]) -> str:
    allowed_set = {item.lower() for item in allowed}
    while True:
        value = input(prompt).strip().lower()
        if value in allowed_set:
            return value
        print(f"Invalid choice. Allowed values: {', '.join(allowed)}")


def _ask_percent():
    while True:
        raw_value = input("Predicted percent (0-100): ").strip()
        try:
            value = float(raw_value)
        except ValueError:
            print("Enter a valid number.")
            continue

        if 0 <= value <= 100:
            return value
        print("Percent must be between 0 and 100.")


def _ask_importance(category_id: str) -> float:
    if category_id in {"1", "2"}:
        return 1.0

    importance_choice = _ask_choice("MID type: 1) Midterm 2) Final > ", ["1", "2"])
    return float(importance_choice)


def _ask_subject(semester_data: Semester) -> str:
    subjects = sorted(semester_data.grades.keys())
    if subjects:
        print("Available subjects:", ", ".join(subjects))
    while True:
        subject = input("Subject: ").strip()
        if subject:
            return subject
        print("Subject cannot be empty.")


def add_predicted_grade_single(
    base_grades: YearData,
    term: int,
    subject: str,
    category_id: str,
    importance: float,
    percent: float,
    title: str = "Predicted grade",
) -> YearData:
    """Return a new YearData with one predicted grade appended. Does not persist."""
    grades = copy.deepcopy(base_grades)

    semester_key = "semester-1" if term == 1 else "semester-2"
    if semester_key not in grades.semesters:
        grades.semesters[semester_key] = Semester(available=True)

    semester_data = grades.semesters[semester_key]
    semester_data.available = True

    new_grade = Grade(
        title=title,
        category_id=category_id,
        importance=importance,
        percent=percent,
        is_manual_override=True,
    )

    semester_data.grades.setdefault(subject, []).append(new_grade)
    return grades


def add_predicted_grade_to_current_year(base_grades: YearData, student_id: str, current_year: int) -> YearData:
    grades = copy.deepcopy(base_grades)

    while True:
        term_choice = _ask_choice("Term (1 or 2): ", ["1", "2"])
        term_num = 1 if term_choice == "1" else 2
        semester_key = "semester-1" if term_num == 1 else "semester-2"

        if semester_key not in grades.semesters:
            grades.semesters[semester_key] = Semester(available=True)

        semester_data = grades.semesters[semester_key]
        semester_data.available = True

        subject = _ask_subject(semester_data)
        category_choice = _ask_choice("Category: 1) FA 2) SA 3) MID > ", ["1", "2", "3"])
        importance = _ask_importance(category_choice)
        percent = _ask_percent()

        grades = add_predicted_grade_single(
            grades,
            term=term_num,
            subject=subject,
            category_id=category_choice,
            importance=importance,
            percent=percent,
        )
        print(f"Added predicted grade to {semester_key}, subject {subject}.")

        should_save = _ask_choice("Save this manual grade permanently to the database? (y/n): ", ["y", "n"])
        if should_save == "y":
            new_grade = grades.semesters[semester_key].grades[subject][-1]
            add_manual_grade_to_db(student_id, current_year, term_num, subject, new_grade)
            print("Grade saved permanently.")

        should_continue = _ask_choice("Add another predicted grade? (y/n): ", ["y", "n"])
        if should_continue == "n":
            return grades


def run_predicted_grades_flow() -> int:
    username, password = get_credentials()
    if not username or not password:
        print("Missing credentials. Set EDUPAGE_USERNAME and EDUPAGE_PASSWORD.")
        return 1

    session = login(username, password)
    if session is None:
        print("Failed to login to EduPage.")
        return 1

    student_id = username.split("@")[0]
    current_year = get_current_year()
    ensure_year_cached(session, student_id, current_year)
    base_grades = load_student_cache(student_id, current_year)
    simulated_current_year_grades = add_predicted_grade_to_current_year(base_grades, student_id, current_year)

    baseline_yearly_gpas, baseline_overall_gpa = calculate_student_gpa_report(session, username)
    scenario_yearly_gpas, scenario_overall_gpa = calculate_student_gpa_report(
        session, username, {current_year: simulated_current_year_grades}
    )

    print("\nBaseline GPA report:")
    print_gpa_report(baseline_yearly_gpas, baseline_overall_gpa)
    print("\nScenario GPA report (with predicted grades):")
    print_gpa_report(scenario_yearly_gpas, scenario_overall_gpa)
    print(f"\nOverall GPA delta: {round(scenario_overall_gpa - baseline_overall_gpa, 2)}")
    return 0


def run_main_loop() -> int:
    while True:
        print("\nChoose action:")
        print("1) Calculate GPA report")
        print("2) Update current year grades")
        print("3) Predicted grades")
        print("q) Quit")

        choice = input("> ").strip().lower()

        if choice == "1":
            run_gpa_flow()
        elif choice == "2":
            run_update_current_year_flow()
        elif choice == "3":
            run_predicted_grades_flow()
        elif choice == "q":
            return 0
        else:
            print("Unknown option. Try again.")


def main() -> int:
    load_dotenv_file(Path(__file__).parent / ".env")
    init_db()
    return run_main_loop()


if __name__ == "__main__":
    raise SystemExit(main())

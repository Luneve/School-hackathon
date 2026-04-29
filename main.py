import os
import copy
from pathlib import Path

from src.utils import get_graduation_year, get_grade_range, get_current_year, get_grade_for_year
from src.calculator import (
    calculate_annual_percent,
    calculate_gpa,
    calculate_overall_gpa_from_years,
    calculate_subject_gpa,
    percent_to_scale,
    gpa_to_letter,
)
from src.cache import (
    load_student_cache,
    save_student_cache,
    is_student_data_cached,
    add_manual_grade_to_db,
)
from src.timetable import get_lpw_dict
from src.edu_api import login, get_all_semesters_data
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
    if is_student_data_cached(student_id, year) or session is None:
        return
    save_student_cache(student_id, year, get_all_semesters_data(session, year))


def update_current_year_grades(session, username: str) -> int:
    student_id = username.split("@")[0]
    current_year = get_current_year()
    save_student_cache(student_id, current_year, get_all_semesters_data(session, current_year))
    return current_year


def calculate_student_gpa_report(
    session, username: str, year_overrides: dict[int, YearData] | None = None
) -> tuple[dict[int, float], float]:
    student_id = username.split("@")[0]
    graduation_year = get_graduation_year(username)
    grade_range = get_grade_range(graduation_year, get_current_year())
    year_overrides = year_overrides or {}

    yearly_annual = {}
    yearly_meta = {}
    yearly_gpas = {}

    for year in grade_range:
        if year in year_overrides:
            grades = year_overrides[year]
        else:
            ensure_year_cached(session, student_id, year)
            grades = load_student_cache(student_id, year)

        annual = calculate_annual_percent(grades)
        meta = get_lpw_dict(student_id, year, get_grade_for_year(graduation_year, year))
        if not meta:
            continue

        yearly_annual[year] = annual
        yearly_meta[year] = meta
        yearly_gpas[year] = calculate_gpa(annual, meta)

    return yearly_gpas, calculate_overall_gpa_from_years(yearly_annual, yearly_meta)


def _build_subject_row(subject: str, annual_data: dict, meta: dict, all_grades: list) -> dict:
    sem1 = annual_data.get("sem1")
    sem2 = annual_data.get("sem2")
    annual = annual_data.get("annual")

    if annual is None:
        status = "not_started"
        letter, gpa_pts = "—", 0.0
    elif sem1 is not None and sem2 is not None:
        status = "complete"
        letter, gpa_pts = percent_to_scale(annual)
    else:
        status = "in_progress"
        letter, gpa_pts = percent_to_scale(annual)

    necessary = meta.get("necessary", True)
    at_risk = status != "not_started" and gpa_pts < 3.0 and necessary

    return {
        "subject": subject,
        "sem1": sem1,
        "sem2": sem2,
        "annual": annual,
        "letter": letter,
        "gpa": gpa_pts,
        "lessons_per_week": meta.get("lessons_per_week", 0),
        "include_in_gpa": necessary,
        "status": status,
        "at_risk": at_risk,
        "breakdown": calculate_subject_gpa(all_grades),
    }


def get_full_dashboard_data(
    session,
    username: str,
    year_overrides: dict[int, YearData] | None = None,
) -> dict:
    student_id = username.split("@")[0]
    graduation_year = get_graduation_year(username)
    current_year = get_current_year()
    grade_range = get_grade_range(graduation_year, current_year)
    year_overrides = year_overrides or {}

    yearly_annual: dict[int, dict] = {}
    yearly_meta: dict[int, dict] = {}
    years_out = []

    for year in grade_range:
        if year in year_overrides:
            year_data = year_overrides[year]
        else:
            ensure_year_cached(session, student_id, year)
            year_data = load_student_cache(student_id, year)

        grade_level = get_grade_for_year(graduation_year, year)
        annual = calculate_annual_percent(year_data)
        meta = get_lpw_dict(student_id, year, grade_level)
        year_gpa = calculate_gpa(annual, meta) if meta else 0.0

        yearly_annual[year] = annual
        yearly_meta[year] = meta

        all_grades_per_subject: dict[str, list] = {}
        for sem in year_data.semesters.values():
            if not sem or not sem.available:
                continue
            for subject, grades in sem.grades.items():
                all_grades_per_subject.setdefault(subject, []).extend(grades)

        all_subjects = sorted(set(annual) | set(all_grades_per_subject))
        subjects_out = [
            _build_subject_row(
                subject,
                annual.get(subject, {}),
                meta.get(subject, {}),
                all_grades_per_subject.get(subject, []),
            )
            for subject in all_subjects
        ]

        years_out.append({
            "year": year,
            "grade_level": grade_level,
            "gpa": year_gpa,
            "letter": gpa_to_letter(year_gpa),
            "is_current": year == current_year,
            "has_meta": bool(meta),
            "subjects": subjects_out,
            "subject_meta": meta,
        })

    overall_gpa = calculate_overall_gpa_from_years(yearly_annual, yearly_meta)
    return {
        "years": years_out,
        "overall_gpa": overall_gpa,
        "overall_letter": gpa_to_letter(overall_gpa),
    }


def get_analytics_data(username: str) -> dict:
    """Compute analytics: GPA trend, top/bottom subjects, letter distribution,
    at-risk list, category averages, totals.
    """
    report = get_full_dashboard_data(None, username)
    years = report["years"]

    gpa_trend = [
        {"year": y["year"], "grade_level": y["grade_level"], "gpa": y["gpa"], "letter": y["letter"]}
        for y in years
    ]

    current_year_data = next((y for y in years if y["is_current"]), None)
    current_subjects = current_year_data["subjects"] if current_year_data else []

    ranked = [s for s in current_subjects if s["status"] != "not_started"]
    ranked_sorted = sorted(ranked, key=lambda s: s["gpa"], reverse=True)
    top_subjects = ranked_sorted[:5]
    bottom_subjects = list(reversed(ranked_sorted[-5:])) if len(ranked_sorted) >= 5 else []

    letter_counts: dict[str, int] = {}
    for y in years:
        for s in y["subjects"]:
            if s["status"] == "not_started":
                continue
            letter_counts[s["letter"]] = letter_counts.get(s["letter"], 0) + 1
    letter_order = ["A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "F"]
    letter_distribution = [
        {"letter": L, "count": letter_counts.get(L, 0)} for L in letter_order
    ]
    max_letter_count = max((c["count"] for c in letter_distribution), default=0)

    at_risk = []
    for y in years:
        for s in y["subjects"]:
            if s["at_risk"]:
                at_risk.append({
                    "year": y["year"],
                    "grade_level": y["grade_level"],
                    "subject": s["subject"],
                    "letter": s["letter"],
                    "gpa": s["gpa"],
                    "annual": s["annual"],
                })

    cat_totals = {"FA": [], "SA": [], "MID": []}
    for y in years:
        for s in y["subjects"]:
            for cat, value in s.get("breakdown", {}).items():
                if cat in cat_totals and value is not None:
                    cat_totals[cat].append(value)
    category_averages = [
        {
            "category": cat,
            "average": round(sum(vals) / len(vals), 1) if vals else 0.0,
            "samples": len(vals),
        }
        for cat, vals in cat_totals.items()
    ]

    total_subjects = sum(len(y["subjects"]) for y in years)
    total_complete = sum(1 for y in years for s in y["subjects"] if s["status"] == "complete")
    total_in_progress = sum(1 for y in years for s in y["subjects"] if s["status"] == "in_progress")
    current_lpw = sum(s["lessons_per_week"] for s in current_subjects if s["include_in_gpa"])

    totals = {
        "years_tracked": len(years),
        "total_subjects": total_subjects,
        "complete": total_complete,
        "in_progress": total_in_progress,
        "at_risk": len(at_risk),
        "current_year_lpw": current_lpw,
    }

    max_yearly_gpa = max((y["gpa"] for y in years if y["gpa"] > 0), default=4.0) or 4.0

    return {
        "overall_gpa": report["overall_gpa"],
        "overall_letter": report["overall_letter"],
        "gpa_trend": gpa_trend,
        "max_yearly_gpa": max_yearly_gpa,
        "top_subjects": top_subjects,
        "bottom_subjects": bottom_subjects,
        "letter_distribution": letter_distribution,
        "max_letter_count": max_letter_count,
        "at_risk": at_risk,
        "category_averages": category_averages,
        "totals": totals,
        "current_year": current_year_data["year"] if current_year_data else None,
    }


def print_gpa_report(yearly_gpas: dict[int, float], overall_gpa: float) -> None:
    if not yearly_gpas:
        print("No yearly GPA could be calculated.")
        return
    for year, gpa in sorted(yearly_gpas.items()):
        print(f"{year} GPA: {gpa}")
    print(f"Overall GPA: {overall_gpa}")


def _login_or_exit() -> tuple[str, object] | None:
    username, password = get_credentials()
    if not username or not password:
        print("Missing credentials. Set EDUPAGE_USERNAME and EDUPAGE_PASSWORD.")
        return None
    session = login(username, password)
    if session is None:
        print("Failed to login to EduPage.")
        return None
    return username, session


def run_gpa_flow() -> int:
    creds = _login_or_exit()
    if creds is None:
        return 1
    username, session = creds
    yearly_gpas, overall_gpa = calculate_student_gpa_report(session, username)
    print_gpa_report(yearly_gpas, overall_gpa)
    return 0


def run_update_current_year_flow() -> int:
    creds = _login_or_exit()
    if creds is None:
        return 1
    username, session = creds
    print(f"Updated cached grades for current year: {update_current_year_grades(session, username)}")
    return 0


def _ask_choice(prompt: str, allowed: list[str]) -> str:
    allowed_set = {item.lower() for item in allowed}
    while True:
        value = input(prompt).strip().lower()
        if value in allowed_set:
            return value
        print(f"Invalid choice. Allowed values: {', '.join(allowed)}")


def _ask_percent() -> float:
    while True:
        try:
            value = float(input("Predicted percent (0-100): ").strip())
        except ValueError:
            print("Enter a valid number.")
            continue
        if 0 <= value <= 100:
            return value
        print("Percent must be between 0 and 100.")


def _ask_importance(category_id: str) -> float:
    if category_id in {"1", "2"}:
        return 1.0
    return float(_ask_choice("MID type: 1) Midterm 2) Final > ", ["1", "2"]))


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
    grades = copy.deepcopy(base_grades)
    semester_key = "semester-1" if term == 1 else "semester-2"
    if semester_key not in grades.semesters:
        grades.semesters[semester_key] = Semester(available=True)

    semester_data = grades.semesters[semester_key]
    semester_data.available = True
    semester_data.grades.setdefault(subject, []).append(Grade(
        title=title,
        category_id=category_id,
        importance=importance,
        percent=percent,
        is_manual_override=True,
    ))
    return grades


def add_predicted_grade_to_current_year(
    base_grades: YearData, student_id: str, current_year: int
) -> YearData:
    grades = copy.deepcopy(base_grades)

    while True:
        term_num = int(_ask_choice("Term (1 or 2): ", ["1", "2"]))
        semester_key = "semester-1" if term_num == 1 else "semester-2"
        if semester_key not in grades.semesters:
            grades.semesters[semester_key] = Semester(available=True)
        grades.semesters[semester_key].available = True

        subject = _ask_subject(grades.semesters[semester_key])
        category = _ask_choice("Category: 1) FA 2) SA 3) MID > ", ["1", "2", "3"])
        importance = _ask_importance(category)
        percent = _ask_percent()

        grades = add_predicted_grade_single(
            grades, term=term_num, subject=subject,
            category_id=category, importance=importance, percent=percent,
        )
        print(f"Added predicted grade to {semester_key}, subject {subject}.")

        if _ask_choice("Save this manual grade permanently? (y/n): ", ["y", "n"]) == "y":
            new_grade = grades.semesters[semester_key].grades[subject][-1]
            add_manual_grade_to_db(student_id, current_year, term_num, subject, new_grade)
            print("Grade saved permanently.")

        if _ask_choice("Add another predicted grade? (y/n): ", ["y", "n"]) == "n":
            return grades


def run_predicted_grades_flow() -> int:
    creds = _login_or_exit()
    if creds is None:
        return 1
    username, session = creds

    student_id = username.split("@")[0]
    current_year = get_current_year()
    ensure_year_cached(session, student_id, current_year)
    base_grades = load_student_cache(student_id, current_year)
    scenario_grades = add_predicted_grade_to_current_year(base_grades, student_id, current_year)

    baseline_yearly, baseline_overall = calculate_student_gpa_report(session, username)
    scenario_yearly, scenario_overall = calculate_student_gpa_report(
        session, username, {current_year: scenario_grades}
    )

    print("\nBaseline GPA report:")
    print_gpa_report(baseline_yearly, baseline_overall)
    print("\nScenario GPA report (with predicted grades):")
    print_gpa_report(scenario_yearly, scenario_overall)
    print(f"\nOverall GPA delta: {round(scenario_overall - baseline_overall, 2)}")
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

import os
import copy
from pathlib import Path

from src.utils import get_graduation_year, get_grade_range, get_current_year, get_grade_for_year
from src.calculator import calculate_annual_percent, calculate_gpa, calculate_overall_gpa_from_years
from src.cache import load_student_cache, save_student_cache, is_student_data_cached, load_metadata
from src.edu_api import login, get_all_semesters_data


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
    if is_student_data_cached(student_id, year):
        return

    data = get_all_semesters_data(session, year)
    save_student_cache(student_id, year, data)


def update_current_year_grades(session, username: str) -> int:
    student_id = username.split("@")[0]
    current_year = get_current_year()
    data = get_all_semesters_data(session, current_year)
    save_student_cache(student_id, current_year, data)
    return current_year


def calculate_student_gpa_report(
    session, username: str, year_overrides: dict[int, dict] | None = None
) -> tuple[dict[int, float], float]:
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
        grade = get_grade_for_year(graduation_year, year)

        try:
            metadata = load_metadata(grade)
        except FileNotFoundError:
            print(f"{year}: missing metadata for grade {grade}, skipping GPA calculation.")
            continue

        yearly_annual_grades[year] = annual_percent
        yearly_metadata[year] = metadata
        yearly_gpas[year] = calculate_gpa(annual_percent, metadata)

    overall_gpa = calculate_overall_gpa_from_years(yearly_annual_grades, yearly_metadata)
    return yearly_gpas, overall_gpa


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


def _ask_subject(semester_data: dict) -> str:
    subjects = sorted(semester_data.get("grades", {}).keys())
    if subjects:
        print("Available subjects:", ", ".join(subjects))
    while True:
        subject = input("Subject: ").strip()
        if subject:
            return subject
        print("Subject cannot be empty.")


def add_predicted_grade_to_current_year(base_grades: dict) -> dict:
    grades = copy.deepcopy(base_grades)

    while True:
        term_choice = _ask_choice("Term (1 or 2): ", ["1", "2"])
        semester_key = "semester-1" if term_choice == "1" else "semester-2"
        semester_data = grades.setdefault(semester_key, {"available": True, "grades": {}})
        semester_data["available"] = True
        semester_data.setdefault("grades", {})

        subject = _ask_subject(semester_data)
        category_choice = _ask_choice("Category: 1) FA 2) SA 3) MID > ", ["1", "2", "3"])
        importance = _ask_importance(category_choice)
        percent = _ask_percent()

        new_grade = {
            "title": "Predicted grade",
            "category_id": category_choice,
            "importance": importance,
            "percent": percent,
        }

        semester_data["grades"].setdefault(subject, []).append(new_grade)
        print(f"Added predicted grade to {semester_key}, subject {subject}.")

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
    simulated_current_year_grades = add_predicted_grade_to_current_year(base_grades)

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
        print("3) Predicted grades (coming soon)")
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
    return run_main_loop()


if __name__ == "__main__":
    raise SystemExit(main())
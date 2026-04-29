from datetime import date, timedelta

from edupage_api import Edupage
from edupage_api.exceptions import BadCredentialsException, CaptchaException
from edupage_api.grades import Term
from src.config import EDUPAGE_DOMAIN
from src.models import Grade, Semester, YearData
from src.utils import safe_float


def login(username, password):
    edupage = Edupage()
    try:
        edupage.login(username, password, EDUPAGE_DOMAIN)
    except BadCredentialsException:
        print("Wrong username or password!")
        return None
    except CaptchaException:
        print("Captcha required!")
        return None
    return edupage


def get_grades(session, year, term) -> dict[str, list[Grade]]:
    grades = session.get_grades_for_term(year=year, term=term)

    grades_per_subject: dict[str, list[Grade]] = {}

    for grade in grades:
        subject = grade.subject_name

        if subject not in grades_per_subject:
            grades_per_subject[subject] = []

        imp = safe_float(grade.importance, default=1.0)
        perc = safe_float(grade.percent, default=0.0)
        class_avg = safe_float(getattr(grade, "average_percent", None), default=0.0)

        grades_per_subject[subject].append(Grade(
            title=str(grade.title) if grade.title is not None else "",
            category_id=str(grade.category_id) if grade.category_id is not None else "",
            importance=imp,
            percent=perc,
            is_manual_override=False,
            class_avg_percent=class_avg,
        ))

    return grades_per_subject


def get_all_semesters_data(session, year: int) -> YearData:
    semester_1_grades = get_grades(session, year, term=Term.FIRST)
    semester_2_grades = get_grades(session, year, term=Term.SECOND)

    return YearData(
        semesters={
            "semester-1": Semester(available=True, grades=semester_1_grades),
            "semester-2": Semester(
                available=bool(semester_2_grades),
                grades=semester_2_grades,
            ),
        }
    )


def get_timetable_lessons_per_week(session, year: int) -> dict[str, int]:
    """
    Samples 7 consecutive school days in October of the given academic year
    and counts how many times each subject appears.

    Returns {subject_name: count} which equals lessons per week for a typical week.
    Falls back to an empty dict if the timetable API is unavailable.

    NOTE: edupage_api timetable support varies by version. If `get_timetable`
    is not available on your installed version, update edupage_api or call
    upsert_subject_meta manually via the Edit UI.
    """
    counts: dict[str, int] = {}

    try:
        # Start from the first Monday of October in the academic year
        sample = date(year, 10, 1)
        # Advance to Monday
        while sample.weekday() != 0:
            sample += timedelta(days=1)

        days_checked = 0
        current = sample
        while days_checked < 7:
            if current.weekday() < 6:  # Mon–Sat (Quantum has some Saturdays)
                try:
                    timetable = session.get_timetable(date=current)
                    for lesson in timetable:
                        subj = getattr(lesson, "subject_name", None)
                        if subj:
                            counts[subj] = counts.get(subj, 0) + 1
                except Exception:
                    pass
                days_checked += 1
            current += timedelta(days=1)
    except Exception:
        return {}

    return counts

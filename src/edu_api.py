"""Login + grade fetching from EduPage."""

from edupage_api import Edupage
from edupage_api.exceptions import BadCredentialsException, CaptchaException
from edupage_api.grades import Term

from src.config import EDUPAGE_DOMAIN
from src.models import Grade, Semester, YearData
from src.utils import safe_float


def login(username: str, password: str) -> Edupage | None:
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


def _grades_for_term(session, year: int, term) -> dict[str, list[Grade]]:
    by_subject: dict[str, list[Grade]] = {}
    for g in session.get_grades_for_term(year=year, term=term):
        by_subject.setdefault(g.subject_name, []).append(Grade(
            title=str(g.title) if g.title is not None else "",
            category_id=str(g.category_id) if g.category_id is not None else "",
            importance=safe_float(g.importance, default=1.0),
            percent=safe_float(g.percent, default=0.0),
            class_avg_percent=safe_float(getattr(g, "average_percent", None), default=0.0),
        ))
    return by_subject


def get_all_semesters_data(session, year: int) -> YearData:
    sem1 = _grades_for_term(session, year, Term.FIRST)
    sem2 = _grades_for_term(session, year, Term.SECOND)
    return YearData(semesters={
        "semester-1": Semester(available=True, grades=sem1),
        "semester-2": Semester(available=bool(sem2), grades=sem2),
    })

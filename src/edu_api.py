from edupage_api import Edupage
from edupage_api.exceptions import BadCredentialsException, CaptchaException
from edupage_api.grades import Term
from src.config import EDUPAGE_DOMAIN


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


def get_grades(session, year, term):
    grades = session.get_grades_for_term(year=year, term=term)

    grades_per_subject = {}

    for grade in grades:
        subject = grade.subject_name

        if subject not in grades_per_subject:
            grades_per_subject[subject] = []

        new_grade = {
            "title": grade.title,
            "category_id": grade.category_id,
            "importance": grade.importance,
            "percent": grade.percent,
        }

        grades_per_subject[subject].append(new_grade)

    return grades_per_subject


def get_all_semesters_data(session, year: int) -> dict:
    semester_1 = get_grades(session, year, term=Term.FIRST)
    semester_2 = get_grades(session, year, term=Term.SECOND)

    return {
        "semester-1": {
            "available": True,
            "grades": semester_1,
        },
        "semester-2": {
            "available": bool(semester_2),
            "grades": semester_2 or {},
        },
    }
import datetime
from typing import Any


def get_graduation_year(email: str) -> int:
    local_part = email.split("@")[0]
    grad_year_short = int(local_part[-2:])
    return 2000 + grad_year_short


def get_current_year() -> int:
    now = datetime.datetime.now()
    return now.year if now.month >= 9 else now.year - 1


def get_grade_range(graduation_year: int, current_year: int) -> list[int]:
    starting_year = graduation_year - 4
    return [year for year in range(starting_year, current_year + 1)]


def get_grade_for_year(graduation_year: int, year: int) -> int:
    starting_year = graduation_year - 4
    return 8 + (year - starting_year)


def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely coerce a value to float, returning default on failure."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default
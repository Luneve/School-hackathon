from src.config import CATEGORY_MAP, WEIGHTS, SCALES


def calculate_subject_gpa(subject_grades: list) -> dict:
    totals = {cat: {"score": 0.0, "weight": 0.0} for cat in WEIGHTS}

    for grade in subject_grades:
        cat = CATEGORY_MAP.get(str(grade.get("category_id")))
        if cat is None:
            continue

        try:
            importance = float(grade.get("importance", 1))
            percent = float(grade.get("percent", 0))
        except (TypeError, ValueError):
            continue

        totals[cat]["score"] += percent * importance
        totals[cat]["weight"] += importance

    return {
        cat: round(data["score"] / data["weight"], 2)
        for cat, data in totals.items()
        if data["weight"] > 0
    }


def calculate_final_grade(cats: dict) -> float | None:
    if not cats:
        return None

    weights = {cat: WEIGHTS[cat] for cat in cats}
    total_weight = sum(weights.values())
    return round(sum(cats[cat] * weights[cat] for cat in cats) / total_weight, 2)


def calculate_annual_percent(grades) -> dict:
    grades = grades
    result = {}

    for semester, sem_key in (("semester-1", "sem1"), ("semester-2", "sem2")):
        sem_data = grades.get(semester, {})
        if not sem_data.get("available", False):
            continue

        for subject, subject_grades in sem_data.get("grades", {}).items():
            final = calculate_final_grade(calculate_subject_gpa(subject_grades))
            result.setdefault(subject, {})[sem_key] = final

    for subject, data in result.items():
        values = [v for v in (data.get("sem1"), data.get("sem2")) if v is not None]
        data["annual"] = round(sum(values) / len(values), 2) if values else None

    return result


def percent_to_scale(percent: float) -> tuple[str, float]:
    percent = round(percent, 0)

    for threshold, letter, gpa in SCALES:
        if percent >= threshold:
            return letter, gpa

    return "F", 0.0


def _calculate_weighted_points(annual_grades: dict, metadata: dict) -> tuple[float, int]:
    result = 0.0
    overall_lessons = 0

    for subject, subject_grades in annual_grades.items():
        percent = subject_grades.get("annual", 0)
        _, scale = percent_to_scale(percent)

        subject_meta = metadata.get(subject)
        if not subject_meta or scale == 0.0 or subject_meta.get("necessary") is False:
            continue

        lessons_per_week = int(subject_meta.get("lessons_per_week", 0))
        if lessons_per_week <= 0:
            continue

        overall_lessons += lessons_per_week
        result += scale * lessons_per_week

    return result, overall_lessons


def calculate_gpa(annual_grades: dict, metadata: dict) -> float:
    result, overall_lessons = _calculate_weighted_points(annual_grades, metadata)

    if overall_lessons == 0:
        return 0.0

    result = round(result / overall_lessons, 2)
    return result


def calculate_overall_gpa_from_years(
    yearly_annual_grades: dict[int, dict], yearly_metadata: dict[int, dict]
) -> float:
    total_points = 0.0
    total_lessons = 0

    for year, annual_grades in yearly_annual_grades.items():
        metadata = yearly_metadata.get(year)
        if not metadata:
            continue

        points, lessons = _calculate_weighted_points(annual_grades, metadata)
        total_points += points
        total_lessons += lessons

    if total_lessons == 0:
        return 0.0

    return round(total_points / total_lessons, 2)
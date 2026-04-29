from dataclasses import dataclass, field
from typing import Any
from src.utils import safe_float


@dataclass
class Grade:
    title: str
    category_id: str
    importance: float
    percent: float
    is_manual_override: bool = False
    class_avg_percent: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Grade":
        imp = safe_float(data.get("importance"), default=1.0)
        perc = safe_float(data.get("percent"), default=0.0)
        class_avg = safe_float(data.get("class_avg_percent"), default=0.0)

        cat = data.get("category_id")
        title = data.get("title")

        return cls(
            title=str(title) if title is not None else "",
            category_id=str(cat) if cat is not None else "",
            importance=imp,
            percent=perc,
            is_manual_override=bool(data.get("is_manual_override", False)),
            class_avg_percent=class_avg,
        )


@dataclass
class Semester:
    available: bool
    grades: dict[str, list[Grade]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Semester":
        grades_data = data.get("grades", {})
        parsed_grades = {
            subj: [Grade.from_dict(g) for g in grades]
            for subj, grades in grades_data.items()
        }
        return cls(
            available=data.get("available", False),
            grades=parsed_grades,
        )


@dataclass
class YearData:
    semesters: dict[str, Semester] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "YearData":
        return cls(
            semesters={
                k: Semester.from_dict(v) for k, v in data.items()
            }
        )

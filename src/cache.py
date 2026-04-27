import json
from src.config import STUDENT_DATA_DIR, METADATA_DIR


def get_student_file_path(student_id: str, year: int) -> str:
    return str(STUDENT_DATA_DIR / student_id / f"{year}.json")


def is_student_data_cached(student_id: str, year: int) -> bool:
    return (STUDENT_DATA_DIR / student_id / f"{year}.json").exists()


def load_student_cache(student_id: str, year: int) -> dict:
    file_path = STUDENT_DATA_DIR / student_id / f"{year}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_student_cache(student_id: str, year: int, data: dict) -> None:
    file_path = STUDENT_DATA_DIR / student_id / f"{year}.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def get_metadata_file_path(grade: int):
    return str(METADATA_DIR / f"grade_{grade}.json")


def load_metadata(grade):
    file_path = METADATA_DIR / f"grade_{grade}.json"
    if not file_path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)
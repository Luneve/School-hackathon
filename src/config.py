from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
STUDENT_DATA_DIR = BASE_DIR / "data" / "students"
METADATA_DIR = BASE_DIR / "data" / "metadata"

# EduPage
EDUPAGE_DOMAIN = "quantumstem"

# Calculator Constants
CATEGORY_MAP = {"1": "FA", "4": "FA",
                "2": "SA", "5": "SA",
                "3": "MID", "6": "MID"
                }

WEIGHTS = {"FA": 0.25,
           "SA": 0.25,
           "MID": 0.50
           }

SCALES = [
        (95, "A+", 4.0),
        (90, "A", 4.0),
        (85, "A-", 3.7),
        (80, "B+", 3.3),
        (70, "B", 3.0),
        (65, "B-", 2.7),
        (60, "C+", 2.3),
        (50, "C", 2.0),
        (40, "C-", 1.7),
        (35, "D+", 1.3),
        (30, "D", 1.0),
        (0,  "F",  0.0),
        ]

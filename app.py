import os

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    Response,
)
from src.edu_api import login as edupage_login
from src.utils import get_current_year, get_graduation_year, get_grade_for_year, get_grade_range
from main import (
    calculate_student_gpa_report,
    update_current_year_grades,
    load_dotenv_file,
    add_predicted_grade_single,
    get_full_dashboard_data,
    get_analytics_data,
)
from src.cache import (
    load_student_cache,
    add_manual_grade_to_db,
    delete_manual_grade,
    get_manual_grades,
    get_last_synced_at,
    upsert_external_year_gpa,
    delete_external_year_gpa,
    get_external_year_gpas,
    get_sync_logs,
)
from src.timetable import set_manual_lpw, delete_manual_lpw
from src.calculator import (
    calculate_annual_percent,
    calculate_subject_gpa,
    percent_to_scale,
    gpa_to_letter,
    calculate_gpa,
)
from src.config import CATEGORY_MAP
from src.models import Grade
from src.db import init_db
from pathlib import Path

CATEGORY_LABEL_MAP = CATEGORY_MAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_login():
    if "username" not in session:
        return redirect(url_for("login"))
    return None


def _student_id() -> str:
    return session["username"].split("@")[0]


def _render_dashboard(prediction=None, reverse_result=None):
    username = session["username"]
    student_id = _student_id()
    current_year = get_current_year()

    try:
        report = get_full_dashboard_data(None, username)
    except Exception as e:
        flash(f"Error building dashboard: {e}", "error")
        report = {"years": [], "overall_gpa": 0.0, "overall_letter": "—"}

    try:
        graduation_year = get_graduation_year(username)
        external_gpas = {e["academic_year"]: e for e in get_external_year_gpas(student_id)}
    except Exception:
        graduation_year = None
        external_gpas = {}

    manual_grades = []
    try:
        manual_grades = get_manual_grades(student_id, current_year)
    except Exception:
        pass

    last_synced = None
    try:
        last_synced = get_last_synced_at(student_id)
    except Exception:
        pass

    subjects_list = []
    for yr in report["years"]:
        if yr["is_current"]:
            subjects_list = [s["subject"] for s in yr["subjects"]]
            break

    at_risk_count = sum(
        1
        for yr in report["years"]
        for s in yr["subjects"]
        if s["at_risk"]
    )

    return render_template(
        "dashboard.html",
        report=report,
        current_year=current_year,
        subjects_list=subjects_list,
        manual_grades=manual_grades,
        last_synced=last_synced,
        prediction=prediction,
        reverse_result=reverse_result,
        friendly_name=student_id,
        category_label_map=CATEGORY_LABEL_MAP,
        external_gpas=external_gpas,
        at_risk_count=at_risk_count,
    )


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

load_dotenv_file(Path(__file__).parent / ".env")
init_db()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "super-secret-key-change-in-production")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        edu_session = edupage_login(username, password)

        if edu_session:
            session["username"] = username
            flash("Successfully logged in.", "success")
            try:
                update_current_year_grades(edu_session, username)
            except Exception as e:
                flash(f"Could not sync grades: {e}", "error")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials or Captcha required. Try again.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
def dashboard():
    redir = _require_login()
    if redir:
        return redir
    return _render_dashboard()


@app.route("/sync", methods=["POST"])
def sync():
    redir = _require_login()
    if redir:
        return redir

    username = session["username"]
    password = request.form.get("password", "").strip()
    if not password:
        flash("Please enter your password to sync.", "error")
        return redirect(url_for("dashboard"))

    edu_session = edupage_login(username, password)
    if edu_session:
        try:
            update_current_year_grades(edu_session, username)
            flash("Synced with EduPage. Timetable updated. Manual overrides preserved.", "success")
        except Exception as e:
            flash(f"Sync error: {e}", "error")
    else:
        flash("Sync failed. Invalid credentials or Captcha required.", "error")

    return redirect(url_for("dashboard"))


@app.route("/sync/log")
def sync_log():
    redir = _require_login()
    if redir:
        return redir
    student_id = _student_id()
    logs = get_sync_logs(student_id, limit=40)
    return render_template("sync_log.html", logs=logs)


@app.route("/analytics")
def analytics():
    redir = _require_login()
    if redir:
        return redir

    username = session["username"]
    student_id = _student_id()

    try:
        data = get_analytics_data(username)
    except Exception as e:
        flash(f"Could not build analytics: {e}", "error")
        return redirect(url_for("dashboard"))

    return render_template(
        "analytics.html",
        analytics=data,
        friendly_name=student_id,
    )


# ---------------------------------------------------------------------------
# What-if: score impact preview
# ---------------------------------------------------------------------------

def _parse_prediction_form():
    subject = (request.form.get("subject") or "").strip()
    if not subject:
        raise ValueError("Subject is required.")

    term = int(request.form.get("term", 2))
    if term not in (1, 2):
        raise ValueError("Term must be 1 or 2.")

    category = request.form.get("category")
    if category not in ("1", "2", "3"):
        raise ValueError("Invalid category.")

    if category in ("1", "2"):
        importance = 1.0
    else:
        mid_type = request.form.get("mid_type", "1")
        importance = 2.0 if mid_type == "2" else 1.0

    percent = float(request.form.get("percent", 0.0))
    if not 0 <= percent <= 100:
        raise ValueError("Percent must be between 0 and 100.")

    return term, subject, category, importance, percent


@app.route("/predict", methods=["POST"])
def predict():
    redir = _require_login()
    if redir:
        return redir

    username = session["username"]
    student_id = _student_id()
    current_year = get_current_year()

    try:
        term, subject, category, importance, percent = _parse_prediction_form()
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))

    try:
        baseline_yearly, baseline_overall = calculate_student_gpa_report(None, username)
    except Exception as e:
        flash(f"Could not compute baseline GPA: {e}", "error")
        return redirect(url_for("dashboard"))

    base_grades = load_student_cache(student_id, current_year)
    scenario_grades = add_predicted_grade_single(
        base_grades,
        term=term,
        subject=subject,
        category_id=category,
        importance=importance,
        percent=percent,
    )

    try:
        scenario_yearly, scenario_overall = calculate_student_gpa_report(
            None, username, {current_year: scenario_grades}
        )
    except Exception as e:
        flash(f"Could not compute scenario GPA: {e}", "error")
        return redirect(url_for("dashboard"))

    yearly_deltas = {
        year: round(scenario_yearly.get(year, 0.0) - baseline_yearly.get(year, 0.0), 2)
        for year in sorted(set(baseline_yearly) | set(scenario_yearly))
    }

    prediction = {
        "form": {
            "subject": subject,
            "term": term,
            "category": category,
            "category_label": CATEGORY_LABEL_MAP.get(category, category),
            "importance": importance,
            "percent": percent,
        },
        "baseline_overall": baseline_overall,
        "scenario_overall": scenario_overall,
        "delta": round(scenario_overall - baseline_overall, 2),
        "baseline_yearly": baseline_yearly,
        "scenario_yearly": scenario_yearly,
        "yearly_deltas": yearly_deltas,
    }

    return _render_dashboard(prediction=prediction)


# ---------------------------------------------------------------------------
# What-if: reverse GPA calculator
# ---------------------------------------------------------------------------

@app.route("/whatif/reverse", methods=["POST"])
def whatif_reverse():
    redir = _require_login()
    if redir:
        return redir

    username = session["username"]
    student_id = _student_id()
    current_year = get_current_year()

    try:
        target_gpa = float(request.form.get("target_gpa", 0))
        if not 0.0 <= target_gpa <= 4.0:
            raise ValueError("Target GPA must be between 0.0 and 4.0.")
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))

    # Collect fixed (complete) weighted sum and variable (in-progress) lesson count
    try:
        from main import get_full_dashboard_data
        report = get_full_dashboard_data(None, username)
    except Exception as e:
        flash(f"Could not compute reverse GPA: {e}", "error")
        return redirect(url_for("dashboard"))

    fixed_points = 0.0
    fixed_lessons = 0
    variable_lessons = 0

    for yr in report["years"]:
        for s in yr["subjects"]:
            lpw = s["lessons_per_week"]
            if lpw <= 0 or not s["include_in_gpa"]:
                continue
            if s["status"] == "complete":
                fixed_points += s["gpa"] * lpw
                fixed_lessons += lpw
            elif s["status"] in ("in_progress", "not_started"):
                variable_lessons += lpw

    total_lessons = fixed_lessons + variable_lessons
    if total_lessons == 0 or variable_lessons == 0:
        flash("Not enough subject data with lessons-per-week set to compute reverse GPA.", "error")
        return redirect(url_for("dashboard"))

    required_points = (target_gpa * total_lessons - fixed_points) / variable_lessons
    required_points = max(0.0, min(4.0, required_points))
    required_letter = gpa_to_letter(required_points)

    reverse_result = {
        "target_gpa": target_gpa,
        "required_gpa_points": round(required_points, 2),
        "required_letter": required_letter,
        "variable_subjects": variable_lessons,
        "total_lessons": total_lessons,
    }

    return _render_dashboard(reverse_result=reverse_result)


# ---------------------------------------------------------------------------
# Manual predictions
# ---------------------------------------------------------------------------

@app.route("/add_manual", methods=["POST"])
def add_manual():
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    current_year = get_current_year()

    try:
        term, subject, category, importance, percent = _parse_prediction_form()
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))

    new_grade = Grade(
        title="Web Prediction",
        category_id=category,
        importance=importance,
        percent=percent,
        is_manual_override=True,
    )
    add_manual_grade_to_db(student_id, current_year, term, subject, new_grade)
    flash(f"Manual prediction added for {subject}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/manual/<int:grade_id>/delete", methods=["POST"])
def delete_manual(grade_id):
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    removed = delete_manual_grade(grade_id, student_id)
    if removed:
        flash("Manual prediction removed.", "success")
    else:
        flash("Could not remove that prediction.", "error")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Subject meta (lessons per week)
# ---------------------------------------------------------------------------

@app.route("/subject/<int:year>/edit", methods=["POST"])
def subject_edit(year):
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    subject = (request.form.get("subject") or "").strip()
    if not subject:
        flash("Subject name is required.", "error")
        return redirect(url_for("dashboard"))

    try:
        lpw = int(request.form.get("lessons_per_week", 0))
        include = request.form.get("include_in_gpa") == "1"
    except (ValueError, TypeError):
        flash("Invalid lessons-per-week value.", "error")
        return redirect(url_for("dashboard"))

    set_manual_lpw(
        student_id=student_id,
        year=year,
        subject=subject,
        lessons_per_week=lpw,
        include_in_gpa=include,
    )
    flash(f"Updated {subject} ({year}): {lpw} lessons/week.", "success")
    return redirect(url_for("dashboard"))


@app.route("/subject/<int:year>/save_all", methods=["POST"])
def subject_save_all(year):
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    subjects = request.form.getlist("subjects")

    updated = 0
    for i, subject in enumerate(subjects):
        subject = subject.strip()
        if not subject:
            continue
        try:
            lpw = int(request.form.get(f"lpw_{i}", 0))
            include = request.form.get(f"include_{i}") == "1"
        except (ValueError, TypeError):
            continue
        set_manual_lpw(
            student_id=student_id,
            year=year,
            subject=subject,
            lessons_per_week=lpw,
            include_in_gpa=include,
        )
        updated += 1

    flash(f"Saved {updated} subject{'s' if updated != 1 else ''} for {year}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/subject/<int:year>/delete", methods=["POST"])
def subject_delete(year):
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    subject = (request.form.get("subject") or "").strip()
    if subject:
        delete_manual_lpw(student_id, year, subject)
        flash(f"Removed {subject} from {year} subject list.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# External year GPA
# ---------------------------------------------------------------------------

@app.route("/external-year/save", methods=["POST"])
def external_year_save():
    redir = _require_login()
    if redir:
        return redir

    student_id = _student_id()
    try:
        academic_year = int(request.form.get("academic_year", 0))
        gpa_points = float(request.form.get("gpa_points", 0))
        if not 0.0 <= gpa_points <= 4.0:
            raise ValueError("GPA must be 0.0–4.0")
        label = (request.form.get("label") or "").strip()
    except (ValueError, TypeError) as e:
        flash(f"Invalid input: {e}", "error")
        return redirect(url_for("dashboard"))

    upsert_external_year_gpa(student_id, academic_year, gpa_points, label)
    flash(f"External year {academic_year} GPA saved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/external-year/<int:year>/delete", methods=["POST"])
def external_year_delete(year):
    redir = _require_login()
    if redir:
        return redir
    delete_external_year_gpa(_student_id(), year)
    flash(f"External year {year} removed.", "success")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)

from pathlib import Path
import re
import sys


HELPER_BLOCK = """
def _normalize_dimension(value: Optional[str], fallback: str) -> str:
    value = (value or "").strip()
    return value or fallback


def _split_major_and_college(raw_major: Optional[str]) -> tuple[str, str]:
    major_value = _normalize_dimension(raw_major, "Unassigned Major")

    college_markers = (
        "\\u5b66\\u9662",
        "\\u4e66\\u9662",
        "\\u5b66\\u90e8",
        "\\u7cfb",
    )
    for marker in college_markers:
        if marker in major_value:
            idx = major_value.find(marker) + len(marker)
            college = major_value[:idx].strip(" /|:-")
            remainder = major_value[idx:].strip(" /|:-")
            return (remainder or major_value, college or "Unassigned College")

    for separator in ("|", "/", "\\\\", "-", "\\u2014", "\\u00b7", "\\uff1a", ":"):
        if separator not in major_value:
            continue
        left, right = [part.strip() for part in major_value.split(separator, 1)]
        if any(marker in left for marker in college_markers) or left.endswith("\\u7cfb"):
            return (_normalize_dimension(right, major_value), left)
        if any(marker in right for marker in college_markers) or right.endswith("\\u7cfb"):
            return (_normalize_dimension(left, major_value), right)

    return major_value, "Unassigned College"


def _build_sunshine_analytics(db: Session) -> dict:
    valid_runs_subquery = (
        db.query(
            models.Activity.user_id.label("user_id"),
            func.count(models.Activity.id).label("valid_runs"),
        )
        .filter(
            models.Activity.type == "run",
            models.Activity.is_valid.is_(True),
        )
        .group_by(models.Activity.user_id)
        .subquery()
    )

    student_rows = (
        db.query(
            models.User.id.label("user_id"),
            models.User.major.label("major"),
            models.Class.id.label("class_id"),
            models.Class.name.label("class_name"),
            func.coalesce(valid_runs_subquery.c.valid_runs, 0).label("valid_runs"),
        )
        .outerjoin(models.Class, models.User.class_id == models.Class.id)
        .outerjoin(valid_runs_subquery, valid_runs_subquery.c.user_id == models.User.id)
        .filter(models.User.role == "student")
        .all()
    )

    class_buckets = {}
    major_buckets = {}
    college_buckets = {}

    total_students = 0
    active_students = 0
    total_valid_runs = 0
    total_score = 0
    passed_students = 0
    class_ids = set()
    majors = set()
    colleges = set()

    def ensure_bucket(container: dict, key: str, label_key: str):
        if key not in container:
            container[key] = {
                label_key: key,
                "total_count": 0,
                "active_students": 0,
                "total_valid_runs": 0,
                "total_score": 0,
                "passed_20_count": 0,
            }
        return container[key]

    for row in student_rows:
        total_students += 1
        valid_runs = int(row.valid_runs or 0)
        score = _sunshine_score(valid_runs)
        is_active = valid_runs > 0
        is_passed = valid_runs >= 20
        class_name = _normalize_dimension(row.class_name, "Unassigned Class")
        major_name, college_name = _split_major_and_college(row.major)

        total_valid_runs += valid_runs
        total_score += score
        if is_active:
            active_students += 1
        if is_passed:
            passed_students += 1
        if row.class_id is not None:
            class_ids.add(row.class_id)
        majors.add(major_name)
        colleges.add(college_name)

        for bucket, label_key, key in (
            (class_buckets, "class_name", class_name),
            (major_buckets, "major", major_name),
            (college_buckets, "college", college_name),
        ):
            item = ensure_bucket(bucket, key, label_key)
            item["total_count"] += 1
            item["total_valid_runs"] += valid_runs
            item["total_score"] += score
            if is_active:
                item["active_students"] += 1
            if is_passed:
                item["passed_20_count"] += 1

    def finalize(rows: dict, label_key: str) -> list[dict]:
        items = []
        for item in rows.values():
            total_count = item["total_count"]
            total_score = item.pop("total_score")
            item["avg_score"] = round(total_score / total_count, 2) if total_count else 0
            item["pass_rate"] = round((item["passed_20_count"] / total_count) * 100, 1) if total_count else 0
            item["valid_runs"] = item["total_valid_runs"]
            items.append(item)
        items.sort(key=lambda entry: (-entry["pass_rate"], -entry["total_valid_runs"], entry[label_key]))
        return items

    overview = {
        "total_students": total_students,
        "active_students": active_students,
        "passed_students": passed_students,
        "total_valid_runs": total_valid_runs,
        "avg_score": round(total_score / total_students, 2) if total_students else 0,
        "pass_rate": round((passed_students / total_students) * 100, 1) if total_students else 0,
        "class_count": len(class_ids),
        "major_count": len(majors),
        "college_count": len(colleges),
    }

    return {
        "overview": overview,
        "class_stats": finalize(class_buckets, "class_name"),
        "major_activity": finalize(major_buckets, "major"),
        "college_activity": finalize(college_buckets, "college"),
    }
""".strip()


DASHBOARD_BLOCK = """
@app.get("/admin/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_admin)):
    sunshine = _build_sunshine_analytics(db)
    total_students = db.query(func.count(models.User.id)).filter(models.User.role == "student").scalar()
    total_teachers = db.query(func.count(models.User.id)).filter(models.User.role == "teacher").scalar()
    total_classes = db.query(func.count(models.Class.id)).scalar()
    pending_health = db.query(models.HealthRequest).filter(models.HealthRequest.status == "pending").count()
    pending_activities = db.query(models.Activity).filter(models.Activity.status == "pending_review").count()
    return {
        "total_students": total_students,
        "total_teachers": total_teachers,
        "total_classes": total_classes,
        "pending_health": pending_health,
        "pending_activities": pending_activities,
        "pending_approvals": pending_health + pending_activities,
        "sunshine_overview": sunshine["overview"],
    }
""".strip()


SUNSHINE_BLOCK = """
@app.get("/admin/sunshine/class-stats")
def get_sunshine_class_stats(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_admin)):
    return _build_sunshine_analytics(db)
""".strip()


def replace_once(text: str, pattern: str, replacement: str, description: str) -> str:
    updated, count = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"failed to update {description}")
    return updated


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: admin_backend_patch.py <path>")
        return 2

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        r"return min\(60 \+ \(valid_count - 20\) \* 2, 100\)\n+app = FastAPI",
        "return min(60 + (valid_count - 20) * 2, 100)\n\n\n" + HELPER_BLOCK + "\n\napp = FastAPI",
        "helper block",
    )
    text = replace_once(
        text,
        r'@app.get\("/admin/dashboard/stats"\)\ndef get_dashboard_stats\(.*?\n\n# Classes',
        DASHBOARD_BLOCK + "\n\n# Classes",
        "dashboard stats block",
    )
    text = replace_once(
        text,
        r'@app.get\("/admin/sunshine/class-stats"\)\ndef get_sunshine_class_stats\(.*?\n\n@app.get\("/admin/import/template/students"\)',
        SUNSHINE_BLOCK + '\n\n@app.get("/admin/import/template/students")',
        "sunshine stats block",
    )

    path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Dynamic request-type field definitions and server-side validation.

The same definitions drive the browser form (rendered by static/js/request-form.js)
and the server validation below, so the two can never drift apart.
"""
import datetime as dt
import json
import os
import re
import secrets

from flask import current_app

# dates: "range" (start+end), "single" (one date), "none"
TYPE_DEFS = {
    "leave": {
        "dates": "range",
        "fields": [
            {"name": "leave_type", "label": "Leave type", "type": "select", "required": True,
             "options": ["Casual leave", "Medical leave", "Emergency leave", "Academic leave", "Other"]},
        ],
    },
    "od": {
        "dates": "range",
        "fields": [
            {"name": "event_name", "label": "Event / activity name", "type": "text", "required": True, "max": 120},
            {"name": "venue", "label": "Venue / organising institution", "type": "text", "required": True, "max": 120},
            {"name": "od_category", "label": "Category", "type": "select", "required": True,
             "options": ["Technical event", "Sports", "Cultural", "Workshop / Seminar", "Official college duty"]},
        ],
    },
    "bonafide": {
        "dates": "none",
        "fields": [
            {"name": "purpose", "label": "Purpose of certificate", "type": "select", "required": True,
             "options": ["Bank account", "Passport", "Scholarship", "Visa", "Internship / Employment", "Other"]},
            {"name": "copies", "label": "Number of copies", "type": "number", "required": True, "min": 1, "max": 5},
        ],
    },
    "permission": {
        "dates": "single",
        "fields": [
            {"name": "from_time", "label": "From time", "type": "time", "required": True},
            {"name": "to_time", "label": "To time", "type": "time", "required": True},
        ],
    },
    "internship": {
        "dates": "range",
        "fields": [
            {"name": "company", "label": "Company / organisation", "type": "text", "required": True, "max": 120},
            {"name": "role_title", "label": "Role / domain", "type": "text", "required": True, "max": 120},
            {"name": "mode", "label": "Mode", "type": "select", "required": True,
             "options": ["On-site", "Remote", "Hybrid"]},
        ],
    },
    "other": {
        "dates": "none",
        "fields": [
            {"name": "category", "label": "Request category", "type": "text", "required": True, "max": 80},
        ],
    },
}

# Request types each requester role may raise
ROLE_TYPES = {
    "student": ["leave", "od", "bonafide", "permission", "internship", "other"],
    "faculty": ["leave", "od", "permission", "other"],
    "hod": ["leave", "od", "permission", "other"],
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _date(value):
    if not value or not DATE_RE.match(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def validate_request_form(form, type_code, allow_past_days=60):
    """Return (cleaned, errors). `cleaned` is ready to persist."""
    spec = TYPE_DEFS[type_code]
    errors, cleaned = {}, {}

    title = (form.get("title") or "").strip()
    reason = (form.get("reason") or "").strip()
    description = (form.get("description") or "").strip()
    if not 5 <= len(title) <= 150:
        errors["title"] = "Title must be 5–150 characters."
    if not 5 <= len(reason) <= 255:
        errors["reason"] = "Reason must be 5–255 characters."
    if len(description) > 2000:
        errors["description"] = "Description can be at most 2000 characters."
    cleaned.update(title=title, reason=reason, description=description,
                   is_confidential=1 if form.get("is_confidential") == "on" else 0)

    start = end = None
    today = dt.date.today()
    if spec["dates"] in ("range", "single"):
        start = _date((form.get("start_date") or "").strip())
        if not start:
            errors["start_date"] = "Enter a valid date."
        elif start < today - dt.timedelta(days=allow_past_days):
            errors["start_date"] = "Date is too far in the past."
        elif start > today + dt.timedelta(days=365):
            errors["start_date"] = "Date is more than a year ahead."
        if spec["dates"] == "range":
            end = _date((form.get("end_date") or "").strip())
            if not end:
                errors["end_date"] = "Enter a valid date."
            elif start and end < start:
                errors["end_date"] = "End date cannot be before the start date."
            elif start and (end - start).days > 180:
                errors["end_date"] = "A request can span at most 180 days."
        else:
            end = start

    extra = {}
    for f in spec["fields"]:
        raw = (form.get(f["name"]) or "").strip()
        label = f["label"]
        if not raw:
            if f.get("required"):
                errors[f["name"]] = "%s is required." % label
            continue
        if f["type"] == "select" and raw not in f["options"]:
            errors[f["name"]] = "Choose a valid option."
        elif f["type"] == "text" and len(raw) > f.get("max", 120):
            errors[f["name"]] = "%s is too long." % label
        elif f["type"] == "number":
            if not raw.isdigit() or not f["min"] <= int(raw) <= f["max"]:
                errors[f["name"]] = "Enter a number between %d and %d." % (f["min"], f["max"])
        elif f["type"] == "time" and not TIME_RE.match(raw):
            errors[f["name"]] = "Enter a valid time."
        extra[f["name"]] = raw
    if type_code == "permission" and "from_time" in extra and "to_time" in extra \
            and "from_time" not in errors and "to_time" not in errors \
            and extra["to_time"] <= extra["from_time"]:
        errors["to_time"] = "End time must be after the start time."

    cleaned["start_date"] = start.isoformat() if start else None
    cleaned["end_date"] = end.isoformat() if end else None
    cleaned["days"] = ((end - start).days + 1) if (start and end) else 0
    cleaned["extra"] = json.dumps(extra)
    return cleaned, errors


# ------------------------------------------------------------- attachments
_MAGIC = {
    "pdf": (b"%PDF",), "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",), "doc": (b"\xd0\xcf\x11\xe0",), "docx": (b"PK\x03\x04",),
}
_MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "doc": "application/msword",
         "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def check_upload(file_storage):
    """Validate an optional upload. Returns (ok, error, info). Never trusts client MIME type."""
    if not file_storage or not file_storage.filename:
        return True, None, None
    name = os.path.basename(file_storage.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in current_app.config["ALLOWED_EXTENSIONS"]:
        return False, "Attachment must be PDF, PNG, JPG, DOC or DOCX.", None
    data = file_storage.read(current_app.config["MAX_ATTACHMENT_BYTES"] + 1)
    file_storage.seek(0)
    if len(data) == 0:
        return False, "The attachment is empty.", None
    if len(data) > current_app.config["MAX_ATTACHMENT_BYTES"]:
        return False, "Attachment must be 5 MB or smaller.", None
    if not any(data.startswith(m) for m in _MAGIC[ext]):
        return False, "File content does not match its extension.", None
    return True, None, {"ext": ext, "size": len(data), "name": name[:255], "mime": _MIME[ext]}


def save_upload(file_storage, info):
    stored = "%s.%s" % (secrets.token_hex(16), info["ext"])
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    file_storage.save(os.path.join(folder, stored))
    return stored

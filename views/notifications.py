"""In-app notifications."""
import math

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from core import login_required, unread_count
from db import get_db
from views.common import page_arg

bp = Blueprint("notif", __name__, url_prefix="/notifications")


@bp.route("")
@login_required
def index():
    db = get_db()
    only_unread = request.args.get("filter") == "unread"
    where = "user_id = %s" + (" AND is_read = 0" if only_unread else "")
    total = db.scalar("SELECT COUNT(*) FROM notifications WHERE " + where, (g.user["id"],)) or 0
    per = 15
    pages = max(1, math.ceil(total / per))
    page = min(page_arg(request.args), pages)
    rows = db.query("SELECT * FROM notifications WHERE " + where + " ORDER BY id DESC LIMIT %s OFFSET %s",
                    (g.user["id"], per, (page - 1) * per))
    return render_template("notifications.html", rows=rows, pg=dict(total=total, page=page, pages=pages),
                           only_unread=only_unread)


@bp.route("/<int:nid>/open")
@login_required
def open_(nid):
    db = get_db()
    n = db.one("SELECT * FROM notifications WHERE id = %s AND user_id = %s", (nid, g.user["id"])) or abort(404)
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = %s", (nid,))
    if n["request_id"]:
        return redirect(url_for("reqs.detail", ref=n["request_id"]))
    return redirect(url_for("notif.index"))


@bp.route("/read-all", methods=["POST"])
@login_required
def read_all():
    get_db().execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s AND is_read = 0", (g.user["id"],))
    flash("All notifications marked as read.", "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("notif.index"))


@bp.route("/count")
@login_required
def count():
    return jsonify(unread=unread_count(g.user["id"]))

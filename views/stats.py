"""Aggregate statistics shared by dashboards and reports (counts only - never request content)."""
import datetime as dt

from db import get_db
from views.common import LIST_FROM, STATUSES, filter_clause, scope_clause

STATUS_COLORS = {"pending": "#f59e0b", "approved": "#16a34a", "rejected": "#dc2626",
                 "returned": "#7c3aed", "cancelled": "#94a3b8"}


def where_for(user, scope, args=None):
    sc, sp = scope_clause(user, scope)
    fw, fp = filter_clause(args or {}, scope)
    return " WHERE " + " AND ".join([sc] + fw), sp + fp


def status_counts(where, params):
    rows = get_db().query("SELECT r.status AS k, COUNT(*) AS n " + LIST_FROM + where + " GROUP BY r.status", params)
    out = {s: 0 for s in STATUSES}
    for r in rows:
        out[r["k"]] = int(r["n"])
    out["total"] = sum(out[s] for s in STATUSES)
    return out


def by_type(where, params):
    return [(r["k"], int(r["n"])) for r in get_db().query(
        "SELECT t.name AS k, COUNT(*) AS n " + LIST_FROM + where + " GROUP BY t.name ORDER BY n DESC", params)]


def by_department(where, params):
    rows = get_db().query("SELECT COALESCE(d.code, 'N/A') AS dept, r.status AS st, COUNT(*) AS n " + LIST_FROM + where +
                          " GROUP BY d.code, r.status", params)
    out = {}
    for r in rows:
        d = out.setdefault(r["dept"], {s: 0 for s in STATUSES} | {"total": 0})
        d[r["st"]] += int(r["n"])
        d["total"] += int(r["n"])
    return sorted(([k, v] for k, v in out.items()), key=lambda kv: -kv[1]["total"])


def trend(where, params, months=6):
    rows = get_db().query("SELECT SUBSTR(r.created_at, 1, 7) AS m, COUNT(*) AS n " + LIST_FROM + where +
                          " GROUP BY SUBSTR(r.created_at, 1, 7)", params)
    have = {r["m"]: int(r["n"]) for r in rows}
    today = dt.date.today().replace(day=1)
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append("%04d-%02d" % (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    return [(dt.date.fromisoformat(k + "-01").strftime("%b"), have.get(k, 0)) for k in keys]


def avg_turnaround_days(where, params):
    rows = get_db().query("SELECT r.created_at AS c, r.completed_at AS d " + LIST_FROM + where +
                          (" AND " if where else " WHERE ") + "r.completed_at IS NOT NULL AND r.status IN "
                          "('approved','rejected') LIMIT 5000", params)
    spans = []
    for r in rows:
        try:
            a = dt.datetime.strptime(r["c"], "%Y-%m-%d %H:%M:%S")
            b = dt.datetime.strptime(r["d"], "%Y-%m-%d %H:%M:%S")
            spans.append((b - a).total_seconds() / 86400)
        except (TypeError, ValueError):
            pass
    return round(sum(spans) / len(spans), 1) if spans else None


def donut(counts):
    return [(s.title(), counts[s], STATUS_COLORS[s]) for s in STATUSES if counts[s]]

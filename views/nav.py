"""Sidebar navigation per role. Items with a `perm` are hidden unless the role holds it
(hiding is cosmetic - every route re-checks authorisation on the server)."""
from flask import url_for


def nav_for(user):
    role, perms = user["role"], user["perms"]
    home = url_for("dashboards." + role)
    items = [("Dashboard", home, "bi-grid-1x2", "dashboards." + role, None)]
    if role in ("student", "faculty", "hod"):
        if role == "faculty":
            items.append(("Review queue", url_for("reqs.queue"), "bi-inbox", "reqs.queue", "request.review"))
        if role == "hod":
            items.append(("Pending queue", url_for("reqs.queue"), "bi-inbox", "reqs.queue", "request.review"))
            items.append(("Department requests", url_for("reqs.department"), "bi-diagram-3", "reqs.department",
                          "request.view_department"))
        items.append(("New request", url_for("reqs.new"), "bi-plus-circle", "reqs.new", "request.create"))
        items.append(("My requests", url_for("reqs.mine"), "bi-journal-text", "reqs.mine", "request.create"))
        if role == "hod":
            items.append(("Reports", url_for("reports.index"), "bi-bar-chart-line", "reports.index", "reports.department"))
    if role == "principal":
        items.append(("Approval queue", url_for("reqs.queue"), "bi-inbox", "reqs.queue", "request.review"))
        items.append(("Routed requests", url_for("reqs.principal_list"), "bi-journal-check", "reqs.principal_list",
                      "request.review"))
        items.append(("Reports", url_for("reports.index"), "bi-bar-chart-line", "reports.index", "reports.college"))
        items.append(("Audit history", url_for("reqs.decision_audit"), "bi-shield-check", "reqs.decision_audit",
                      "audit.decisions"))
    if role == "admin":
        items += [
            ("Users", url_for("admin.users"), "bi-people", "admin.users", "users.manage"),
            ("Departments", url_for("admin.departments"), "bi-building", "admin.departments", "departments.manage"),
            ("Roles & permissions", url_for("admin.roles"), "bi-key", "admin.roles", "roles.manage"),
            ("Approval workflow", url_for("admin.workflow_rules"), "bi-signpost-split", "admin.workflow_rules", "workflow.manage"),
            ("All requests", url_for("reqs.all_requests"), "bi-collection", "reqs.all_requests", "request.view_all"),
            ("Reports", url_for("reports.index"), "bi-bar-chart-line", "reports.index", "reports.college"),
            ("System activity", url_for("admin.activity"), "bi-activity", "admin.activity", "audit.system"),
        ]
    items.append(("Notifications", url_for("notif.index"), "bi-bell", "notif.index", None))
    return [dict(label=l, url=u, icon=i, endpoint=e) for (l, u, i, e, p) in items if p is None or p in perms]

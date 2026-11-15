"""Blueprint registry."""


def register_blueprints(app):
    from views.auth import bp as auth
    from views.dashboards import bp as dashboards
    from views.requests_bp import bp as reqs
    from views.notifications import bp as notif
    from views.reports import bp as reports
    from views.admin import bp as admin
    for bp in (auth, dashboards, reqs, notif, reports, admin):
        app.register_blueprint(bp)

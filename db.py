"""Thin database layer.

Production target is MySQL via PyMySQL. A SQLite backend exists purely as a
zero-install development/test fallback; all SQL in this project is written in
the portable subset so both work. Dates/timestamps are stored as ISO strings.
"""
import datetime as _dt
import os
import re
import sqlite3

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql", "schema.sql")


def now_str():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm(value):
    if isinstance(value, _dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, _dt.date):
        return value.strftime("%Y-%m-%d")
    return value


class Database:
    def __init__(self, conn, backend):
        self.conn = conn
        self.backend = backend

    def _sql(self, sql):
        return sql.replace("%s", "?") if self.backend == "sqlite" else sql

    def _exec(self, sql, args=()):
        cur = self.conn.cursor()
        cur.execute(self._sql(sql), tuple(args))
        return cur

    def query(self, sql, args=()):
        cur = self._exec(sql, args)
        rows = cur.fetchall()
        cur.close()
        return [{k: _norm(v) for k, v in dict(r).items()} for r in rows]

    def one(self, sql, args=()):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def scalar(self, sql, args=()):
        row = self.one(sql, args)
        return next(iter(row.values())) if row else None

    def execute(self, sql, args=()):
        cur = self._exec(sql, args)
        last = cur.lastrowid
        cur.close()
        return last

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def connect(app=None, with_database=True):
    cfg = (app or current_app).config
    if cfg["DB_BACKEND"] == "sqlite":
        conn = sqlite3.connect(cfg["SQLITE_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return Database(conn, "sqlite")
    import pymysql  # imported lazily so SQLite dev mode needs no extra package
    kwargs = dict(
        host=cfg["MYSQL_HOST"], port=cfg["MYSQL_PORT"], user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"], charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )
    if with_database:
        kwargs["database"] = cfg["MYSQL_DB"]
    return Database(pymysql.connect(**kwargs), "mysql")


def get_db():
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        try:
            if exc is None:
                db.commit()
            else:
                db.rollback()
        finally:
            db.close()


def _split_statements(script):
    script = re.sub(r"--[^\n]*", "", script)
    return [s.strip() for s in script.split(";") if s.strip()]


def _to_sqlite(stmt):
    stmt = re.sub(r"INT AUTO_INCREMENT PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", stmt)
    stmt = re.sub(r"\)\s*ENGINE=.*$", ")", stmt, flags=re.S)
    stmt = re.sub(r"TINYINT\(1\)", "INTEGER", stmt)
    return stmt


def init_schema(app, drop_existing=False):
    """Create all tables from sql/schema.sql."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        statements = _split_statements(fh.read())
    if app.config["DB_BACKEND"] == "sqlite":
        path = app.config["SQLITE_PATH"]
        if drop_existing and os.path.exists(path):
            os.remove(path)
        db = connect(app)
        for stmt in statements:
            if stmt.upper().startswith(("CREATE DATABASE", "USE ")):
                continue
            db.execute(_to_sqlite(stmt))
        db.commit()
        db.close()
        return
    db = connect(app, with_database=False)
    if drop_existing:
        db.execute("DROP DATABASE IF EXISTS `%s`" % app.config["MYSQL_DB"])
    for stmt in statements:
        db.execute(stmt)
    db.commit()
    db.close()

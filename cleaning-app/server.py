#!/usr/bin/env python3
"""Chore points app: a tiny JSON API + static file server backed by SQLite.

No dependencies beyond the Python 3.9+ standard library.

Environment variables:
    HOST     address to listen on      (default 0.0.0.0)
    PORT     port to listen on         (default 8080)
    DB_PATH  path to the SQLite file   (default ./data/chores.db)
"""

import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(HERE, "public")
DB_PATH = os.environ.get("DB_PATH", os.path.join(HERE, "data", "chores.db"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8080"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id     INTEGER PRIMARY KEY,
    name   TEXT    NOT NULL,
    color  TEXT    NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS chores (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    points     INTEGER NOT NULL,
    every_days INTEGER,              -- NULL = no schedule ("anytime")
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rewards (
    id     INTEGER PRIMARY KEY,
    name   TEXT    NOT NULL,
    cost   INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
-- points/cost are copied at the time of the event, so editing a chore
-- or reward later never rewrites anyone's history.
CREATE TABLE IF NOT EXISTS completions (
    id        INTEGER PRIMARY KEY,
    chore_id  INTEGER NOT NULL REFERENCES chores(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    points    INTEGER NOT NULL,
    at        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS redemptions (
    id        INTEGER PRIMARY KEY,
    reward_id INTEGER NOT NULL REFERENCES rewards(id),
    person_id INTEGER NOT NULL REFERENCES people(id),
    cost      INTEGER NOT NULL,
    at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_completions_chore  ON completions(chore_id, at);
CREATE INDEX IF NOT EXISTS idx_completions_person ON completions(person_id, at);
CREATE INDEX IF NOT EXISTS idx_redemptions_person ON redemptions(person_id);
"""

PALETTE = ["#2563eb", "#db2777", "#16a34a", "#ea580c", "#7c3aed", "#0891b2", "#ca8a04", "#dc2626"]


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with closing(connect()) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)


def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def need_str(body, key, max_len=80):
    val = body.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ApiError(400, f"'{key}' is required")
    return val.strip()[:max_len]


def need_int(body, key, lo=0, hi=1_000_000, optional=False):
    val = body.get(key)
    if optional and val in (None, ""):
        return None
    try:
        val = int(val)
    except (TypeError, ValueError):
        raise ApiError(400, f"'{key}' must be a whole number")
    if not lo <= val <= hi:
        raise ApiError(400, f"'{key}' must be between {lo} and {hi}")
    return val


def balance(db, person_id):
    return db.execute(
        "SELECT COALESCE((SELECT SUM(points) FROM completions WHERE person_id = ?), 0)"
        "     - COALESCE((SELECT SUM(cost)   FROM redemptions WHERE person_id = ?), 0)",
        (person_id, person_id),
    ).fetchone()[0]


def get_active(db, table, item_id):
    row = db.execute(f"SELECT * FROM {table} WHERE id = ? AND active = 1", (item_id,)).fetchone()
    if row is None:
        raise ApiError(404, f"No such {table[:-1]}")
    return row


# --------------------------------------------------------------------------- routes

ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


@route("GET", "/api/state")
def state(db, body):
    now = int(time.time())
    people = rows(db, """
        SELECT p.id, p.name, p.color,
               COALESCE((SELECT SUM(points) FROM completions WHERE person_id = p.id), 0)
             - COALESCE((SELECT SUM(cost)   FROM redemptions WHERE person_id = p.id), 0) AS balance,
               COALESCE((SELECT SUM(points) FROM completions
                          WHERE person_id = p.id AND at >= ?), 0) AS week,
               COALESCE((SELECT SUM(points) FROM completions WHERE person_id = p.id), 0) AS lifetime
        FROM people p WHERE p.active = 1 ORDER BY p.name COLLATE NOCASE""", (now - 7 * 86400,))
    chores = rows(db, """
        SELECT c.id, c.name, c.points, c.every_days, last.at AS last_at, last.person_id AS last_by
        FROM chores c
        LEFT JOIN completions last ON last.id = (
            SELECT id FROM completions WHERE chore_id = c.id ORDER BY at DESC, id DESC LIMIT 1)
        WHERE c.active = 1 ORDER BY c.name COLLATE NOCASE""")
    rewards = rows(db, "SELECT id, name, cost FROM rewards WHERE active = 1 ORDER BY cost, name")
    history = rows(db, """
        SELECT * FROM (
            SELECT 'completion' AS kind, x.id, x.person_id, p.name AS person, c.name AS what,
                   x.points AS delta, x.at
            FROM completions x JOIN chores c ON c.id = x.chore_id JOIN people p ON p.id = x.person_id
            UNION ALL
            SELECT 'redemption', r.id, r.person_id, p.name, w.name, -r.cost, r.at
            FROM redemptions r JOIN rewards w ON w.id = r.reward_id JOIN people p ON p.id = r.person_id
        ) ORDER BY at DESC, id DESC LIMIT 100""")
    return {"now": now, "people": people, "chores": chores, "rewards": rewards, "history": history}


@route("POST", "/api/people")
def add_person(db, body):
    name = need_str(body, "name", 40)
    count = db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    cur = db.execute("INSERT INTO people (name, color) VALUES (?, ?)", (name, PALETTE[count % len(PALETTE)]))
    return {"id": cur.lastrowid}


@route("POST", "/api/chores")
def add_chore(db, body):
    cur = db.execute(
        "INSERT INTO chores (name, points, every_days) VALUES (?, ?, ?)",
        (need_str(body, "name"), need_int(body, "points", 0, 10_000),
         need_int(body, "every_days", 1, 365, optional=True)))
    return {"id": cur.lastrowid}


@route("POST", "/api/rewards")
def add_reward(db, body):
    cur = db.execute("INSERT INTO rewards (name, cost) VALUES (?, ?)",
                     (need_str(body, "name"), need_int(body, "cost", 1, 1_000_000)))
    return {"id": cur.lastrowid}


EDITABLE = {
    "people": {"name": lambda b: need_str(b, "name", 40)},
    "chores": {"name": lambda b: need_str(b, "name"),
               "points": lambda b: need_int(b, "points", 0, 10_000),
               "every_days": lambda b: need_int(b, "every_days", 1, 365, optional=True)},
    "rewards": {"name": lambda b: need_str(b, "name"),
                "cost": lambda b: need_int(b, "cost", 1, 1_000_000)},
}


@route("PATCH", "/api/(people|chores|rewards)/(\\d+)")
def edit_item(db, body, table, item_id):
    get_active(db, table, item_id)
    fields = {k: parse(body) for k, parse in EDITABLE[table].items() if k in body}
    if not fields:
        raise ApiError(400, "Nothing to change")
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), item_id))
    return {"ok": True}


@route("DELETE", "/api/(people|chores|rewards)/(\\d+)")
def archive_item(db, body, table, item_id):
    # Archived rather than deleted, so history still shows the right names.
    get_active(db, table, item_id)
    db.execute(f"UPDATE {table} SET active = 0 WHERE id = ?", (item_id,))
    return {"ok": True}


@route("POST", "/api/complete")
def complete(db, body):
    chore = get_active(db, "chores", need_int(body, "chore_id", 1, 2**62))
    person = get_active(db, "people", need_int(body, "person_id", 1, 2**62))
    cur = db.execute("INSERT INTO completions (chore_id, person_id, points, at) VALUES (?, ?, ?, ?)",
                     (chore["id"], person["id"], chore["points"], int(time.time())))
    return {"id": cur.lastrowid, "points": chore["points"]}


@route("POST", "/api/redeem")
def redeem(db, body):
    reward = get_active(db, "rewards", need_int(body, "reward_id", 1, 2**62))
    person = get_active(db, "people", need_int(body, "person_id", 1, 2**62))
    have = balance(db, person["id"])
    if have < reward["cost"]:
        raise ApiError(400, f"{person['name']} needs {reward['cost'] - have} more points")
    cur = db.execute("INSERT INTO redemptions (reward_id, person_id, cost, at) VALUES (?, ?, ?, ?)",
                     (reward["id"], person["id"], reward["cost"], int(time.time())))
    return {"id": cur.lastrowid}


@route("DELETE", "/api/(completions|redemptions)/(\\d+)")
def undo(db, body, table, item_id):
    if db.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,)).rowcount == 0:
        raise ApiError(404, "Already gone")
    return {"ok": True}


# --------------------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".webmanifest": "application/manifest+json",
        ".svg": "image/svg+xml",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/backup":
            return self.send_backup()
        if path.startswith("/api/"):
            return self.handle_api("GET")
        return super().do_GET()

    def do_POST(self):
        self.handle_api("POST")

    def do_PATCH(self):
        self.handle_api("PATCH")

    def do_DELETE(self):
        self.handle_api("DELETE")

    def end_headers(self):
        # Always revalidate, so a `git pull` + restart reaches every phone.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, method):
        path = self.path.split("?", 1)[0]
        try:
            for m, pattern, fn in ROUTES:
                match = pattern.match(path)
                if m == method and match:
                    break
            else:
                raise ApiError(404, "Not found")
            body = {}
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                if length > 64_000:
                    raise ApiError(413, "Request too large")
                try:
                    body = json.loads(self.rfile.read(length))
                except ValueError:
                    raise ApiError(400, "Invalid JSON")
                if not isinstance(body, dict):
                    raise ApiError(400, "Expected a JSON object")
            args = [int(g) if g.isdigit() else g for g in match.groups()]
            with closing(connect()) as db:
                with db:  # one transaction per request
                    result = fn(db, body, *args)
            self.send_json(200, result)
        except ApiError as e:
            self.send_json(e.status, {"error": e.message})
        except Exception as e:  # keep the server alive, report the problem
            self.log_error("API error: %r", e)
            self.send_json(500, {"error": "Server error"})

    def send_backup(self):
        # A consistent copy of the live database, safe to take while running.
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with closing(connect()) as src, closing(sqlite3.connect(tmp)) as dst:
                src.backup(dst)
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            os.remove(tmp)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Disposition",
                         f'attachment; filename="chores-{time.strftime("%Y-%m-%d")}.db"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Chore points running on http://{HOST}:{PORT}  (database: {DB_PATH})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

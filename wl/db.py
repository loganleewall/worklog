"""SQLite storage.

One row per block of work you report.  A block lives inside a single local day
and is stored as minutes since midnight, so nothing here depends on timezones
or DST.  Blocks on the same day may not overlap -- an hour worked is counted
once.
"""
import os
import sqlite3
import time
from datetime import date, datetime, timedelta

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT    NOT NULL,                -- local YYYY-MM-DD
    start_min  INTEGER NOT NULL,                -- minutes since midnight, 0..1439
    end_min    INTEGER NOT NULL,                -- 1..1440, > start_min
    title      TEXT    NOT NULL DEFAULT '',
    project    TEXT    NOT NULL DEFAULT '',
    rating     INTEGER,                         -- 1..5, NULL = not rated
    notes      TEXT    NOT NULL DEFAULT '',
    created_ts REAL    NOT NULL,
    updated_ts REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blocks_day ON blocks(day, start_min);

CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    slot INTEGER                                -- stable colour slot 1..4, else NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

FIELDS = ("day", "start_min", "end_min", "title", "project", "rating", "notes")


class Invalid(ValueError):
    """The block as submitted cannot be stored."""


class Overlap(Invalid):
    """The block collides with one already logged that day."""


def connect(path=None):
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=15.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    con.commit()
    return con


def today_str():
    return date.today().isoformat()


def fmt_min(m):
    return "%d:%02d" % (m // 60, m % 60)


# ------------------------------------------------------------------ settings

def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    con.commit()


def goal_minutes(con):
    try:
        hours = float(get_setting(con, "goal_hours", config.DEFAULT_GOAL_HOURS))
    except ValueError:
        hours = config.DEFAULT_GOAL_HOURS
    return int(round(hours * 60))


# -------------------------------------------------------------------- blocks

def clean(data):
    """Validate a submitted block; returns the row to store or raises Invalid."""
    try:
        day = datetime.strptime(str(data.get("day", "")), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise Invalid("date must look like YYYY-MM-DD")
    try:
        start, end = int(data["start_min"]), int(data["end_min"])
    except (KeyError, TypeError, ValueError):
        raise Invalid("start and end times are required")
    if not 0 <= start < 1440 or not 0 < end <= 1440:
        raise Invalid("times must fall within one day")
    if end <= start:
        raise Invalid("end must be after start -- for work past midnight,"
                      " add a second block on the next day")
    rating = data.get("rating")
    if rating in ("", None):
        rating = None
    else:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            raise Invalid("rating must be 1-5")
        if not 1 <= rating <= 5:
            raise Invalid("rating must be 1-5")
    text = lambda k, n: " ".join(str(data.get(k) or "").split())[:n]
    return {"day": day, "start_min": start, "end_min": end,
            "title": text("title", 200), "project": text("project", 60),
            "rating": rating, "notes": str(data.get("notes") or "").strip()[:4000]}


def _check_overlap(con, row, skip_id=None):
    hit = con.execute(
        "SELECT * FROM blocks WHERE day=? AND start_min < ? AND end_min > ? AND id != ?"
        " ORDER BY start_min LIMIT 1",
        (row["day"], row["end_min"], row["start_min"], skip_id or -1)).fetchone()
    if hit:
        raise Overlap("overlaps %s (%s-%s)" % (
            '"%s"' % hit["title"] if hit["title"] else "another block",
            fmt_min(hit["start_min"]), fmt_min(hit["end_min"])))


def _ensure_project(con, name):
    if con.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
        return
    used = {r["slot"] for r in con.execute("SELECT slot FROM projects WHERE slot IS NOT NULL")}
    free = [s for s in range(1, config.COLOR_SLOTS + 1) if s not in used]
    con.execute("INSERT INTO projects (name, slot) VALUES (?,?)", (name, free[0] if free else None))


def _prune_projects(con):
    # A colour belongs to a project for as long as it has blocks; once the last
    # one is gone the slot is free for whatever you start next.
    con.execute("DELETE FROM projects WHERE name NOT IN (SELECT DISTINCT project FROM blocks)")


def get_block(con, block_id):
    row = con.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
    return dict(row) if row else None


def create_block(con, data):
    row = clean(data)
    _check_overlap(con, row)
    _ensure_project(con, row["project"])
    now = time.time()
    cur = con.execute(
        "INSERT INTO blocks (day, start_min, end_min, title, project, rating, notes,"
        " created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?)",
        tuple(row[f] for f in FIELDS) + (now, now))
    con.commit()
    return get_block(con, cur.lastrowid)


def update_block(con, block_id, data):
    old = get_block(con, block_id)
    if not old:
        return None
    row = clean(dict(old, **data))
    _check_overlap(con, row, skip_id=block_id)
    _ensure_project(con, row["project"])
    con.execute(
        "UPDATE blocks SET day=?, start_min=?, end_min=?, title=?, project=?, rating=?,"
        " notes=?, updated_ts=? WHERE id=?",
        tuple(row[f] for f in FIELDS) + (time.time(), block_id))
    _prune_projects(con)
    con.commit()
    return get_block(con, block_id)


def delete_block(con, block_id):
    n = con.execute("DELETE FROM blocks WHERE id=?", (block_id,)).rowcount
    _prune_projects(con)
    con.commit()
    return bool(n)


def list_blocks(con, start="0000-01-01", end="9999-12-31"):
    return [dict(r) for r in con.execute(
        "SELECT * FROM blocks WHERE day >= ? AND day <= ? ORDER BY day, start_min",
        (start, end))]


def project_slots(con):
    return {r["name"]: r["slot"] for r in con.execute("SELECT name, slot FROM projects")}


# --------------------------------------------------------------------- stats

def week_bounds(day):
    """Monday..Sunday of the week containing `day`."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    mon = d - timedelta(days=d.weekday())
    return mon.isoformat(), (mon + timedelta(days=6)).isoformat()


def total_minutes(con, start, end):
    return con.execute("SELECT COALESCE(SUM(end_min - start_min), 0) FROM blocks"
                       " WHERE day >= ? AND day <= ?", (start, end)).fetchone()[0]


def streak(con, today=None):
    """Consecutive days with work logged, ending today (or yesterday, so the
    streak is not shown as broken before you have logged anything today)."""
    d = datetime.strptime(today or today_str(), "%Y-%m-%d").date()
    days = {r[0] for r in con.execute("SELECT DISTINCT day FROM blocks WHERE day <= ?",
                                      (d.isoformat(),))}
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def overview(con, today=None):
    today = today or today_str()
    w0, w1 = week_bounds(today)
    return {"today": today,
            "today_min": total_minutes(con, today, today),
            "week_min": total_minutes(con, w0, w1),
            "streak": streak(con, today),
            "goal_min": goal_minutes(con)}

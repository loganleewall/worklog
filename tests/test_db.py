"""Checks for the rules the calendar rests on.

Run with:  python3 -m tests.test_db
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wl import cli, db

DAY = "2026-05-14"   # a Thursday


def fresh():
    return db.connect(os.path.join(tempfile.mkdtemp(), "t.db"))


def add(con, start, end, day=DAY, **kw):
    return db.create_block(con, dict(day=day, start_min=start, end_min=end, **kw))


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    return False


def test_validation():
    con = fresh()
    assert raises(db.Invalid, add, con, 600, 600)             # zero length
    assert raises(db.Invalid, add, con, 700, 600)             # ends before it starts
    assert raises(db.Invalid, add, con, 600, 1500)            # past midnight
    assert raises(db.Invalid, add, con, 600, 660, rating=6)
    assert raises(db.Invalid, add, con, 600, 660, day="yesterday")
    b = add(con, 1380, 1440, title="  late   night ", rating="")
    assert b["title"] == "late night" and b["rating"] is None and b["end_min"] == 1440


def test_overlap():
    con = fresh()
    a = add(con, 540, 600)
    assert raises(db.Overlap, add, con, 570, 630)
    assert raises(db.Overlap, add, con, 500, 700)
    add(con, 600, 660)                                        # touching is fine
    add(con, 540, 600, day="2026-05-15")                      # other days are separate
    assert db.update_block(con, a["id"], {"end_min": 590})["end_min"] == 590  # not vs itself
    assert raises(db.Overlap, db.update_block, con, a["id"], {"end_min": 610})


def test_project_colours_follow_the_project():
    con = fresh()
    ids = [add(con, 60 * i, 60 * i + 30, project=p)["id"]
           for i, p in enumerate(["a", "b", "c", "d", "e"])]
    assert db.project_slots(con) == {"a": 1, "b": 2, "c": 3, "d": 4, "e": None}
    db.delete_block(con, ids[1])                              # b is gone, slot 2 is free
    slots = db.project_slots(con)
    assert "b" not in slots and slots["c"] == 3               # survivors are not repainted
    add(con, 600, 630, project="f")
    assert db.project_slots(con)["f"] == 2


def test_overview_and_streak():
    con = fresh()
    assert db.week_bounds(DAY) == ("2026-05-11", "2026-05-17")
    add(con, 540, 660, day="2026-05-11")
    add(con, 540, 600, day="2026-05-12")
    add(con, 540, 600, day="2026-05-13")
    o = db.overview(con, DAY)
    assert o["today_min"] == 0 and o["week_min"] == 240
    assert o["streak"] == 3                                   # today not logged yet: still alive
    add(con, 540, 570)
    assert db.streak(con, DAY) == 4
    assert db.streak(con, "2026-05-16") == 0                  # a full day missed


def test_parse_time():
    assert cli.parse_time("9") == 540 and cli.parse_time("9:30") == 570
    assert cli.parse_time("2pm") == 840 and cli.parse_time("12am") == 0
    assert cli.parse_time("12:15pm") == 735 and cli.parse_time("24:00") == 1440
    assert raises(db.Invalid, cli.parse_time, "25:00")
    assert raises(db.Invalid, cli.parse_time, "noon")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  %s" % name)

# worklog

A local, self-reported work log. You add blocks of work to a calendar, give
each one a rating, and look back at the history. Nothing is tracked
automatically and nothing leaves the machine.

Sibling of `../screentime`: same stack (Python standard library + SQLite +
vanilla JS, no dependencies), same look.

```
./worklog install      # run the server at login, forever
./worklog dash         # open http://127.0.0.1:8850
```

## The UI

- **Week** — a calendar grid. Drag on empty space to draw a block, click for a
  one-hour block, drag a block to move it (across days too), drag its bottom
  edge to resize, click it to edit. Everything snaps to 15 minutes.
- **Month** — hours per day, with the day's average rating. Click a day to open
  its week.
- **Log** — every block of the month as a table, with CSV export.

A block has a title, a date, start and end times, an optional **rating** from
1 (distracted) to 5 (deep focus), an optional **project** and notes. The first
four projects each get their own colour and keep it; later ones are grey.

Blocks on the same day cannot overlap, so an hour is never counted twice, and a
block cannot cross midnight (log the part after midnight on the next day).
Average ratings are weighted by time. The streak counts consecutive days with
anything logged and is not broken until a whole day is missed.

Keys: `←` `→` move through time, `T` jumps to today, `N` adds a block.

## Commands

| Command | What it does |
|---|---|
| `./worklog dash` | open the calendar (starting the server if needed) |
| `./worklog add 9:30 11 "problem set" -r 4 -p classes` | log a block from the terminal |
| `./worklog report [day\|week] [--date YYYY-MM-DD]` | print logged blocks |
| `./worklog status` | is it running, plus today's numbers |
| `./worklog stop` | stop the server |
| `./worklog install` | hand the server to launchd: starts at login, restarts if it dies |
| `./worklog uninstall` | remove the login item and the agent's copy of the code |
| `./worklog serve` | run the server in the foreground |

Every command takes `--db PATH` and `--port N`.

`install` copies the `wl` package to `~/Library/Application Support/worklog`
and runs it from there, because macOS does not let a background agent read
`~/Desktop`. **Re-run `./worklog install` after changing the code.**

## Where things live

```
~/.worklog/worklog.db   every block (SQLite)
~/.worklog/server.log   server output
wl/db.py                schema, validation, overlap rule, streak
wl/server.py            JSON API + static files, 127.0.0.1 only
wl/cli.py               commands, launchd install
wl/web/                 the calendar
tests/test_db.py        python3 -m tests.test_db
```

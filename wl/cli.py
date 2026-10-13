"""Command line entry point."""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime

from . import config, db

PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCH_LABEL = "com.worklog.server"
# ~/Desktop, ~/Documents and ~/Downloads are gated by macOS privacy controls,
# and a launchd agent gets no chance to answer the prompt. The agent runs from
# a copy of the package here instead.
APP_SUPPORT = os.path.expanduser("~/Library/Application Support/worklog")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/%s.plist" % LAUNCH_LABEL)


# ------------------------------------------------------------ process utils

def _url(port=None):
    return "http://%s:%d" % (config.SERVER_HOST, port or config.SERVER_PORT)


def _ping(port=None):
    """True if a worklog server (not just anything) answers on the port."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(_url(port) + "/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "worklog"
    except Exception:
        return False


def _wait_up(port, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _ping(port):
            return True
        time.sleep(0.2)
    return False


def _agent_pid():
    """PID of the launchd-managed server, or None if the agent isn't loaded."""
    r = subprocess.run(["launchctl", "list", LAUNCH_LABEL],
                       capture_output=True, text=True, check=False)
    if r.returncode:
        return None
    m = re.search(r'"PID"\s*=\s*(\d+)', r.stdout)
    return int(m.group(1)) if m else 0     # 0 = loaded but not currently up


def _pid_file(port):
    return os.path.join(config.DATA_DIR, "server-%d.pid" % int(port))


def _read_pid(port):
    try:
        with open(_pid_file(port)) as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _spawn(port):
    config.ensure_data_dir()
    log = open(config.LOG_PATH, "a")
    env = dict(os.environ, PYTHONPATH=PKG_PARENT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "wl.cli", "serve", "--db", config.DB_PATH,
         "--port", str(port)],
        cwd=PKG_PARENT, env=env, stdout=log, stderr=log,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    with open(_pid_file(port), "w") as fh:
        fh.write(str(proc.pid))
    return proc.pid


def _kill(port):
    pid = _read_pid(port)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        os.unlink(_pid_file(port))
    except OSError:
        pass
    return pid


def _deploy():
    """Copy the package somewhere a background agent is allowed to read."""
    dst = os.path.join(APP_SUPPORT, "wl")
    os.makedirs(APP_SUPPORT, exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(os.path.join(PKG_PARENT, "wl"), dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return APP_SUPPORT


# ----------------------------------------------------------------- printing

def fmt_dur(minutes):
    h, m = divmod(int(minutes), 60)
    if h and m:
        return "%dh %02dm" % (h, m)
    return "%dh" % h if h else "%dm" % m


def parse_time(text):
    """'9', '9:30', '14:15', '2pm', '2:30pm' -> minutes since midnight."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip().lower())
    if not m:
        raise db.Invalid("cannot read time %r (try 9:30, 14:15 or 2pm)" % text)
    h, mins, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half:
        if not 1 <= h <= 12:
            raise db.Invalid("cannot read time %r" % text)
        h = h % 12 + (12 if half == "pm" else 0)
    if h > 24 or mins > 59 or (h == 24 and mins):
        raise db.Invalid("cannot read time %r" % text)
    return h * 60 + mins


def _print_blocks(blocks):
    day = None
    for b in blocks:
        if b["day"] != day:
            day = b["day"]
            total = sum(x["end_min"] - x["start_min"] for x in blocks if x["day"] == day)
            print("\n\033[1m%s\033[0m  %s" % (
                datetime.strptime(day, "%Y-%m-%d").strftime("%a %b %d"), fmt_dur(total)))
        print("  %5s-%-5s  %-7s  %s  %s%s" % (
            db.fmt_min(b["start_min"]), db.fmt_min(b["end_min"]),
            fmt_dur(b["end_min"] - b["start_min"]),
            ("★ %d" % b["rating"]) if b["rating"] else "   ",
            b["title"] or "Work",
            "  [%s]" % b["project"] if b["project"] else ""))


# ----------------------------------------------------------------- commands

def cmd_serve(args):
    from . import server
    try:
        httpd = server.serve(args.host, args.port)
    except OSError as exc:
        print("cannot listen on %s:%s — %s\n"
              "something is already using that port; try --port %d"
              % (args.host, args.port, exc, int(args.port) + 1))
        return 1
    print("worklog on http://%s:%d" % httpd.server_address[:2], flush=True)
    httpd.serve_forever()


def cmd_dash(args):
    if not _ping(args.port):
        if os.path.exists(PLIST_PATH) and args.port == _installed_port():
            subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True, check=False)
        else:
            _spawn(args.port)
        if not _wait_up(args.port):
            print("the server did not come up — see %s" % config.LOG_PATH)
            return 1
    print(_url(args.port))
    if not args.no_open:
        webbrowser.open(_url(args.port))


def cmd_stop(args):
    stopped = False
    if _agent_pid() is not None:
        subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True, check=False)
        stopped = True
    stopped = bool(_kill(args.port)) or stopped
    print("stopped" if stopped else "nothing was running")
    if os.path.exists(PLIST_PATH):
        print("it will start again at next login; './worklog uninstall' removes it for good")


def cmd_status(args):
    apid = _agent_pid()
    if _ping(args.port):
        how = ("pid %d, started at login by launchd" % apid if apid
               else "started by hand — will not survive a reboot; run ./worklog install")
        print("server    running (%s) %s" % (how, _url(args.port)))
    else:
        print("server    not running — ./worklog dash starts it")
    con = db.connect()
    o = db.overview(con)
    print("database  %s" % config.DB_PATH)
    print("today     %s of a %s goal" % (fmt_dur(o["today_min"]), fmt_dur(o["goal_min"])))
    print("this week %s" % fmt_dur(o["week_min"]))
    print("streak    %d day%s" % (o["streak"], "" if o["streak"] == 1 else "s"))


def cmd_add(args):
    con = db.connect()
    try:
        b = db.create_block(con, {
            "day": args.date or db.today_str(),
            "start_min": parse_time(args.start), "end_min": parse_time(args.end),
            "title": args.title, "project": args.project, "rating": args.rating,
            "notes": args.notes})
    except db.Invalid as exc:
        print("not added: %s" % exc)
        return 1
    _print_blocks([b])


def cmd_report(args):
    con = db.connect()
    anchor = args.date or db.today_str()
    start, end = (anchor, anchor) if args.period == "day" else db.week_bounds(anchor)
    blocks = db.list_blocks(con, start, end)
    if not blocks:
        print("nothing logged for %s" % (start if start == end else "%s to %s" % (start, end)))
        return
    _print_blocks(blocks)
    total = sum(b["end_min"] - b["start_min"] for b in blocks)
    rated = [b for b in blocks if b["rating"]]
    line = "\ntotal %s" % fmt_dur(total)
    if rated:
        w = sum(b["end_min"] - b["start_min"] for b in rated)
        line += "   avg rating %.1f" % (
            sum(b["rating"] * (b["end_min"] - b["start_min"]) for b in rated) / w)
    print(line)


def _installed_port():
    try:
        with open(PLIST_PATH) as fh:
            m = re.search(r"<string>--port</string><string>(\d+)</string>", fh.read())
        return int(m.group(1)) if m else None
    except OSError:
        return None


def cmd_install(args):
    """Keep the server running: start at login, restart if it dies."""
    config.ensure_data_dir()
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True, check=False)
    _kill(args.port)                      # a hand-started server would hold the port
    run_dir = _deploy()
    plist = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string><string>-u</string><string>-m</string><string>wl.cli</string>
    <string>serve</string><string>--db</string><string>{db}</string>
    <string>--port</string><string>{port}</string>
  </array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONPATH</key><string>{cwd}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
""".format(label=LAUNCH_LABEL, python=sys.executable, cwd=run_dir,
           db=config.DB_PATH, port=args.port, log=config.LOG_PATH)
    with open(PLIST_PATH, "w") as fh:
        fh.write(plist)
    r = subprocess.run(["launchctl", "load", PLIST_PATH], capture_output=True, text=True)
    if r.returncode:
        print("launchctl load failed: %s" % (r.stderr.strip() or r.stdout.strip()))
        return 1
    print("installed %s" % PLIST_PATH)
    print("server runs from %s" % run_dir)
    if not _wait_up(args.port, timeout=15):
        print("\033[31mthe agent loaded but the server never answered.\033[0m")
        print("check %s, then './worklog uninstall' to back it out." % config.LOG_PATH)
        return 1
    print("worklog is up at %s: it starts at every login and restarts if it dies."
          % _url(args.port))
    print("re-run install after changing the code -- the agent uses its own copy.")


def cmd_uninstall(args):
    if os.path.exists(PLIST_PATH):
        subprocess.run(["launchctl", "unload", PLIST_PATH], capture_output=True, check=False)
        os.unlink(PLIST_PATH)
        print("removed %s" % PLIST_PATH)
    else:
        print("no launch agent installed")
    if os.path.exists(APP_SUPPORT):
        shutil.rmtree(APP_SUPPORT)
        print("removed %s" % APP_SUPPORT)
    print("your log in %s is untouched" % config.DB_PATH)


# --------------------------------------------------------------------- main

def build_parser():
    # --db / --port live on every subcommand, so `worklog dash --port N` works.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=config.DB_PATH, help="database file (default: %(default)s)")
    common.add_argument("--port", type=int, default=config.SERVER_PORT, help="server port")

    p = argparse.ArgumentParser(prog="worklog",
                                description="Local self-reported work log with a calendar UI.")
    sub = p.add_subparsers(dest="cmd", required=True)
    add = lambda name, help_: sub.add_parser(name, help=help_, parents=[common])

    s = add("dash", "open the calendar (starting the server if needed)")
    s.add_argument("--no-open", action="store_true", help="do not open a browser")
    s.set_defaults(func=cmd_dash)

    add("stop", "stop the server").set_defaults(func=cmd_stop)
    add("status", "is it running + today's numbers").set_defaults(func=cmd_status)

    s = add("add", "log a block from the terminal")
    s.add_argument("start", help="start time, e.g. 9:30 or 2pm")
    s.add_argument("end", help="end time")
    s.add_argument("title", nargs="?", default="", help="what you worked on")
    s.add_argument("-r", "--rating", type=int, choices=range(1, 6), help="1-5")
    s.add_argument("-p", "--project", default="")
    s.add_argument("-n", "--notes", default="")
    s.add_argument("--date", help="YYYY-MM-DD (default: today)")
    s.set_defaults(func=cmd_add)

    s = add("report", "print logged blocks in the terminal")
    s.add_argument("period", nargs="?", default="day", choices=["day", "week"])
    s.add_argument("--date", help="anchor date YYYY-MM-DD (default: today)")
    s.set_defaults(func=cmd_report)

    s = add("serve", "run the server in the foreground")
    s.add_argument("--host", default=config.SERVER_HOST)
    s.set_defaults(func=cmd_serve)

    add("install", "run the server automatically at login").set_defaults(func=cmd_install)
    add("uninstall", "remove the login item").set_defaults(func=cmd_uninstall)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    config.DB_PATH = args.db
    config.SERVER_PORT = args.port
    config.ensure_data_dir()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""Paths and tunable defaults. Everything lives under ~/.worklog."""
import os

DATA_DIR = os.path.expanduser("~/.worklog")
DB_PATH = os.path.join(DATA_DIR, "worklog.db")
LOG_PATH = os.path.join(DATA_DIR, "server.log")

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8850

DEFAULT_GOAL_HOURS = 6.0    # daily goal shown on the Today tile; editable in the UI
COLOR_SLOTS = 4             # projects past the first four render in the neutral colour


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR

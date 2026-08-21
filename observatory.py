"""
The telescope.

Agents cannot see any of this. It exists so that after an eight hour run you
can reconstruct exactly who touched what, when, and whether a fact crossed
from one desk to another.
"""
import hashlib
import os
import sqlite3
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    ts       REAL,
    tick     INTEGER,
    agent    TEXT,
    kind     TEXT,
    name     TEXT,
    payload  TEXT
);
CREATE TABLE IF NOT EXISTS journal (
    id     INTEGER PRIMARY KEY,
    ts     REAL,
    tick   INTEGER,
    agent  TEXT,
    text   TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id     INTEGER PRIMARY KEY,
    ts     REAL,
    tick   INTEGER,
    path   TEXT,
    sha    TEXT,
    size   INTEGER
);
CREATE TABLE IF NOT EXISTS contacts (
    id     INTEGER PRIMARY KEY,
    ts     REAL,
    tick   INTEGER,
    agent  TEXT,
    other  TEXT,
    action TEXT,
    path   TEXT
);
CREATE TABLE IF NOT EXISTS markers (
    id     INTEGER PRIMARY KEY,
    ts     REAL,
    tick   INTEGER,
    agent  TEXT,
    item   TEXT,
    marker TEXT,
    where_ TEXT,
    snippet TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_tick ON events(tick);
CREATE INDEX IF NOT EXISTS idx_snap_tick   ON snapshots(tick);
"""

PATH_KEYS = ("path", "source", "destination")
WRITE_TOOLS = {"write_file", "append_file", "move_path", "delete_path", "make_dir"}


def _rel_from_value(value):
    if not isinstance(value, str) or not value:
        return None
    root = os.path.realpath(config.WORKSPACE)
    raw = value if os.path.isabs(value) else os.path.join(root, value)
    try:
        real = os.path.realpath(raw)
    except OSError:
        return value.replace("\\", "/")
    if real == root:
        return "."
    if real.startswith(root + os.sep):
        return os.path.relpath(real, root)
    # Path may not exist yet (write_file). Fall back to lexical.
    text = value.replace("\\", "/").lstrip("./")
    if text.startswith(root):
        return os.path.relpath(text, root)
    return text


def owner_of(rel):
    if not rel:
        return None
    parts = rel.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "agents" and parts[1]:
        return parts[1]
    return None


class Observatory:
    def __init__(self, path=None):
        os.makedirs(config.ROOT, exist_ok=True)
        self.db = sqlite3.connect(path or config.DB_PATH)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.tick = 0

    def set_meta(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
            (key, str(value)),
        )
        self.db.commit()

    def event(self, agent, kind, name, payload):
        self.db.execute(
            "INSERT INTO events (ts, tick, agent, kind, name, payload) "
            "VALUES (?,?,?,?,?,?)",
            (time.time(), self.tick, agent, kind, name, str(payload)[:20000]),
        )
        self.db.commit()

    def journal(self, agent, text):
        self.db.execute(
            "INSERT INTO journal (ts, tick, agent, text) VALUES (?,?,?,?)",
            (time.time(), self.tick, agent, text),
        )
        self.db.commit()

    def last_actions(self, agent, limit=14):
        rows = self.db.execute(
            "SELECT kind, name, payload FROM events "
            "WHERE agent = ? AND kind IN ('tool_call','tool_result') "
            "ORDER BY id DESC LIMIT ?", (agent, limit),
        ).fetchall()
        if not rows:
            return "(no record of previous actions)"

        out = []
        for kind, name, payload in reversed(rows):
            text = (payload or "").replace("\n", " ")[:180]
            if kind == "tool_call":
                out.append(f"  called {name}({text})")
            else:
                out.append(f"    -> {text}")
        return "\n".join(out)

    def contact(self, agent, tool_name, args):
        """If this call touches another agent's directory, log it."""
        if not isinstance(args, dict):
            return
        for key in PATH_KEYS:
            value = args.get(key)
            rel = _rel_from_value(value) if value else None
            if rel is None:
                continue
            other = owner_of(rel)
            world = rel in (".", "") or rel == "agents" or rel.startswith("agents/")
            if other and other != agent:
                kind = "write" if tool_name in WRITE_TOOLS else "read"
                if tool_name == "list_dir":
                    kind = "list"
                elif tool_name == "grep":
                    kind = "grep"
                elif tool_name == "move_path":
                    kind = "move"
                self.db.execute(
                    "INSERT INTO contacts (ts, tick, agent, other, action, path) "
                    "VALUES (?,?,?,?,?,?)",
                    (time.time(), self.tick, agent, other, kind, rel),
                )
                self.db.commit()
                self.event(agent, "contact", kind, f"{other}:{rel}")
            elif world and not other:
                self.event(agent, "world_scan", tool_name, rel)

        # grep with no path defaults to the whole workspace.
        if tool_name == "grep" and not args.get("path"):
            self.event(agent, "world_scan", "grep", ".")

    def contact_from_hits(self, agent, text):
        """Grep/list output can name another agent's files without the
        path argument pointing there. Those hits still count as contact."""
        if not text:
            return
        for line in str(text).splitlines():
            if "agents/" not in line:
                continue
            start = line.find("agents/")
            chunk = line[start:].split()[0].rstrip(":")
            rel = chunk.split(":")[0]
            other = owner_of(rel)
            if other and other != agent:
                self.db.execute(
                    "INSERT INTO contacts (ts, tick, agent, other, action, path) "
                    "VALUES (?,?,?,?,?,?)",
                    (time.time(), self.tick, agent, other, "hit", rel),
                )
                self.db.commit()
                self.event(agent, "contact", "hit", f"{other}:{rel}")

    def markers_in(self, agent, where, text):
        """Record when a plant marker appears in something an agent wrote or saw."""
        if not text:
            return
        blob = str(text)
        low = blob.lower()
        for item in config.ITEMS:
            for marker in item["markers"]:
                if marker.lower() not in low:
                    continue
                snippet = blob.replace("\n", " ")[:240]
                self.db.execute(
                    "INSERT INTO markers (ts, tick, agent, item, marker, where_, snippet) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (time.time(), self.tick, agent, item["name"], marker, where, snippet),
                )
                self.db.commit()

    def snapshot(self):
        """Hash every file in the workspace so changes can be diffed later."""
        rows = []
        for dirpath, dirnames, filenames in os.walk(config.WORKSPACE):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                try:
                    with open(full, "rb") as fh:
                        blob = fh.read()
                except OSError:
                    continue
                rows.append((
                    time.time(), self.tick,
                    os.path.relpath(full, config.WORKSPACE),
                    hashlib.sha256(blob).hexdigest()[:16],
                    len(blob),
                ))
        if rows:
            self.db.executemany(
                "INSERT INTO snapshots (ts, tick, path, sha, size) VALUES (?,?,?,?,?)",
                rows,
            )
        self.db.commit()
        return len(rows)

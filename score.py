#!/usr/bin/env python3
"""
Score a run from observatory.sqlite.

  python3 score.py           # current world (~/antfarm2)
  python3 score.py --watch   # refresh every few seconds

The numbers that matter:

  writes      real files put on disk (the v1 bottleneck)
  contact     an agent touched another agent's directory
  transfer    an agent wrote a plant marker they were not seeded with
  protocol    filenames that look like messages
  fabrication notes that claim actions despite a mechanical ledger
"""
import argparse
import json
import os
import re
import sqlite3
import textwrap
import time

import config

WRITE_TOOLS = ("write_file", "append_file", "move_path", "delete_path", "make_dir")
PROTOCOL_RE = re.compile(
    r"(^|[/_-])(msg|memo|to|from|re|board|inbox|outbox|letter|note)([/_-]|$)",
    re.I,
)

# Who could know a marker without ever leaving home / the corpus.
# searcher owns the Halcyon leftover; clerk owns the Pellhurst key;
# anyone who searches the corpus can find Kellinger, Brantwood, and the
# SG-19 fragment (but not PH-SG19-C / restickered).
OWNERS = {
    "KV-3140-RA-8821": {"corpus"},
    "MEMO-77-4412": {"corpus"},
    "HD8B-1000": {"searcher"},
    "Supercedes Model 8A": {"searcher"},
    "PH-SG19-C": {"clerk"},
    "restickered": {"clerk"},
    "VT-2290-TRI": set(),
    "Verrick Tri-Flow": set(),
    "OSG-LAT-440": set(),
    "Lattice Regulator Type IV": set(),
}


def connect():
    if not os.path.exists(config.DB_PATH):
        raise SystemExit(f"no observatory at {config.DB_PATH} — run python3 run.py --reset")
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def meta(db):
    try:
        rows = db.execute("SELECT key, value FROM meta").fetchall()
        return {r["key"]: r["value"] for r in rows}
    except sqlite3.OperationalError:
        return {}


def counts(db):
    agents = [a["name"] for a in config.AGENTS]
    out = {a: {"writes": 0, "calls": 0, "empty": 0, "claims": 0} for a in agents}

    rows = db.execute(
        "SELECT agent, name, COUNT(*) n FROM events "
        "WHERE kind='tool_call' GROUP BY agent, name"
    ).fetchall()
    for r in rows:
        if r["agent"] not in out:
            continue
        out[r["agent"]]["calls"] += r["n"]
        if r["name"] in WRITE_TOOLS:
            out[r["agent"]]["writes"] += r["n"]

    # Empty shifts: journal entries whose mechanical body has no tools.
    for r in db.execute("SELECT agent, text FROM journal").fetchall():
        if r["agent"] not in out:
            continue
        if "(no tools called)" in (r["text"] or ""):
            out[r["agent"]]["empty"] += 1

    for r in db.execute(
        "SELECT agent, COUNT(*) n FROM events WHERE kind='claim' GROUP BY agent"
    ).fetchall():
        if r["agent"] in out:
            out[r["agent"]]["claims"] = r["n"]

    return out


def contacts(db):
    try:
        rows = db.execute(
            "SELECT tick, agent, other, action, path FROM contacts ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def transfers(db):
    """Agent wrote a marker they were not seeded with."""
    found = []
    try:
        rows = db.execute(
            "SELECT tick, agent, item, marker, where_, snippet FROM markers "
            "WHERE where_ IN ('write','say','journal') ORDER BY id"
        ).fetchall()
    except sqlite3.OperationalError:
        return found
    seen = set()
    for r in rows:
        owners = OWNERS.get(r["marker"], set())
        if r["agent"] in owners:
            continue
        key = (r["agent"], r["marker"])
        if key in seen:
            continue
        seen.add(key)
        found.append(dict(r))
    return found


def protocol_files(db):
    """Filenames that look like they were meant for someone else."""
    try:
        rows = db.execute(
            "SELECT DISTINCT path FROM snapshots ORDER BY id DESC LIMIT 5000"
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    hits = []
    seen = set()
    for r in rows:
        path = r["path"]
        if path in seen:
            continue
        seen.add(path)
        base = os.path.basename(path)
        if PROTOCOL_RE.search(base) or PROTOCOL_RE.search(path):
            # corpus memos are named memo_NN_ — ignore those
            if path.startswith("corpus" + os.sep):
                continue
            hits.append(path)
    return hits


TOOL_LINE = re.compile(r"^(\w+)\((.*)\)\s*->\s*(.*)$")
ARG_KEYS = ("query", "pattern", "url", "source", "destination", "path")


def _focus_arg(inside):
    """Pick the one argument a human actually needs to see."""
    for key in ARG_KEYS:
        m = re.search(rf"(?:^|, ){key}=(.*?)(?=, [a-z_]+=|$)", inside)
        if m:
            return m.group(1).strip()[:72]
    return ""


def parse_shift(text):
    """Split a mechanical journal entry into (tools, note)."""
    tools, note = [], ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("[tick"):
            continue
        if line.lower().startswith("note:"):
            note = line[5:].strip()
            continue
        if line == "(no tools called)":
            tools.append(("(nothing)", ""))
            continue
        m = TOOL_LINE.match(line)
        if m:
            tools.append((m.group(1), _focus_arg(m.group(2))))
        else:
            tools.append((line[:72], ""))
    return tools, note


ANSI = re.compile(r"\033\[[0-9;]*m")
DIM = "\033[2m"
BOLD = "\033[1m"
BOX_W = 72
INNER = BOX_W - 2


def vislen(s):
    return len(ANSI.sub("", s or ""))


def clip_vis(s, n):
    if vislen(s) <= n:
        return s
    out, nseen = [], 0
    i = 0
    while i < len(s) and nseen < n - 1:
        if s[i] == "\033":
            m = ANSI.match(s, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        out.append(s[i])
        nseen += 1
        i += 1
    return "".join(out) + "…"


def pad(s, width, align="left"):
    s = clip_vis(s or "", width)
    gap = width - vislen(s)
    if gap < 0:
        gap = 0
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    if align == "right":
        return " " * gap + s
    return s + " " * gap


def _d(s):
    return f"{DIM}{s}\033[0m"


def box_row(content, left="│", right="│"):
    return _d(left) + pad(content, INNER) + _d(right)


def box_lr(left, right, l="│", r="│"):
    gap = INNER - vislen(left) - vislen(right)
    if gap < 1:
        return box_row(left, l, r)
    return _d(l) + left + " " * gap + right + _d(r)


def single_rule(left="├", right="┤"):
    return _d(left + "─" * INNER + right)


def double_rule(left="╠", right="╣"):
    return _d(left + "═" * INNER + right)


def banner(meta=None):
    meta = meta or {}
    profile = meta.get("profile", "?")
    discovery = meta.get("discovery", "?")
    memory = meta.get("memory", "?")
    lines = [
        _d("╔" + "═" * INNER + "╗"),
        box_row(pad("ANTFARM", INNER, "center"), "║", "║"),
        box_row(pad("four agents · one filesystem", INNER, "center"), "║", "║"),
        double_rule(),
        box_lr(f"  profile {profile}", f"discovery {discovery}   memory {memory}  ", "║", "║"),
        box_row("  shift log · ctrl-c stops this view, not the farm", "║", "║"),
        _d("╚" + "═" * INNER + "╝"),
    ]
    return "\n".join(lines)


def status_box(db):
    t = tally(db)
    m = meta(db)
    c = counts(db)
    top = _d("╔" + "═" * INNER + "╗")
    stats = (f"  tick {t['tick'] if t['tick'] is not None else '—'}    "
             f"writes {t['writes']}    calls {t['calls']}    "
             f"contact {t['contact']}    transfer {t['transfer']}")
    mid = box_row(stats, "║", "║")
    per = "  " + "  ·  ".join(
        f"{name} {row['writes']}w/{row['calls']}c" for name, row in c.items()
    )
    bot_stats = box_row(clip_vis(per, INNER), "║", "║")
    foot = box_lr(
        f"  {m.get('profile', '?')}",
        f"discovery {m.get('discovery', '?')}  ",
        "║", "║",
    )
    end = _d("╚" + "═" * INNER + "╝")
    return "\n".join([top, mid, bot_stats, foot, end])


def format_shift(agent, tick, text, *, color="", reset=""):
    """One shift as a boxed card: title, DID, THINKS."""
    tools, note = parse_shift(text)
    reset = reset or "\033[0m"
    name = f"{color}{BOLD}{(agent or '').upper()}{reset}"
    foreign = any(
        arg and "agents/" in arg and f"agents/{agent}" not in arg
        for _, arg in tools
    )
    flag = f"{BOLD} CONTACT {reset}" if foreign else ""

    lines = [
        _d("┌" + "─" * INNER + "┐"),
        box_lr(f"  TICK {tick}    {name}", flag),
        single_rule(),
        box_row(f"  {BOLD}DID{reset}"),
    ]
    if not tools:
        lines.append(box_row("    (nothing)"))
    else:
        for tool, arg in tools:
            raw = f"    {tool:<12} {arg}".rstrip()
            wrapped = textwrap.wrap(raw, INNER - 1, subsequent_indent=" " * 17) or [raw]
            for w in wrapped:
                lines.append(box_row(w))
    lines.append(single_rule())
    lines.append(box_row(f"  {BOLD}THINKS{reset}"))
    thought = " ".join((note or "—").split())
    for w in textwrap.wrap(thought, INNER - 5) or ["—"]:
        lines.append(box_row("    " + w))
    lines.append(_d("└" + "─" * INNER + "┘"))
    return "\n".join(lines)


def last_shifts(db):
    """Most recent closed shift per agent."""
    out = {}
    try:
        rows = db.execute(
            "SELECT agent, tick, text FROM journal "
            "WHERE id IN (SELECT MAX(id) FROM journal GROUP BY agent)"
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        tools, note = parse_shift(r["text"])
        out[r["agent"]] = {
            "tick": r["tick"],
            "tools": tools,
            "note": note,
            "text": r["text"],
        }
    return out


def recent_journals(db, limit=12):
    try:
        rows = db.execute(
            "SELECT id, tick, agent, text FROM journal ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return list(reversed(rows))


def tally(db):
    c = counts(db)
    writes = sum(r["writes"] for r in c.values())
    calls = sum(r["calls"] for r in c.values())
    tick_row = db.execute("SELECT MAX(tick) t FROM events").fetchone()
    tick = tick_row["t"] if tick_row else None
    try:
        n_contact = db.execute("SELECT COUNT(*) n FROM contacts").fetchone()["n"]
    except sqlite3.OperationalError:
        n_contact = 0
    n_trans = len(transfers(db))
    return {
        "tick": tick,
        "writes": writes,
        "calls": calls,
        "contact": n_contact,
        "transfer": n_trans,
    }


def tally_line(db):
    return status_box(db)


def firsts(db):
    out = {}
    try:
        row = db.execute("SELECT MIN(tick) t FROM contacts").fetchone()
        if row and row["t"] is not None:
            out["contact"] = row["t"]
    except sqlite3.OperationalError:
        pass
    row = db.execute(
        "SELECT MIN(tick) t FROM events WHERE kind='world_scan'"
    ).fetchone()
    if row and row["t"] is not None:
        out["world_scan"] = row["t"]
    row = db.execute(
        "SELECT MIN(tick) t FROM events WHERE kind='citation'"
    ).fetchone()
    if row and row["t"] is not None:
        out["citation"] = row["t"]
    return out


def render(db):
    m = meta(db)
    c = counts(db)
    cons = contacts(db)
    trans = transfers(db)
    fs = firsts(db)

    lines = [banner(m), "", status_box(db), ""]
    bits = []
    if fs.get("contact") is not None:
        bits.append(f"first contact tick {fs['contact']}")
    if fs.get("world_scan") is not None:
        bits.append(f"first scan tick {fs['world_scan']}")
    if fs.get("citation") is not None:
        bits.append(f"first citation tick {fs['citation']}")
    if trans:
        bits.append(f"{len(trans)} transfer(s)")
    if bits:
        lines.append(_d("  " + " · ".join(bits)))
        lines.append("")
    if cons:
        ev = cons[-1]
        lines.append(_d(f"  last contact  {ev['agent']} {ev['action']} {ev['other']}: {ev['path']}"))
        lines.append("")

    recent = recent_journals(db, 8)
    if recent:
        lines.append(_d("─" + " recent " + "─" * (INNER - 8)))
        lines.append("")
        for r in recent:
            lines.append(format_shift(r["agent"], r["tick"], r["text"]))
            lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.json:
        db = connect()
        print(json.dumps({
            "meta": meta(db),
            "counts": counts(db),
            "contacts": contacts(db),
            "transfers": transfers(db),
            "protocol": protocol_files(db),
            "firsts": firsts(db),
            "last_shifts": last_shifts(db),
        }, indent=2, default=str))
        return

    if args.watch:
        while True:
            os.system("clear")
            print(render(connect()))
            time.sleep(4)
    else:
        print(render(connect()))


if __name__ == "__main__":
    main()

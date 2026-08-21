#!/usr/bin/env python3
"""
Live view. Run in a second terminal alongside run.py.

  python3 watch.py            # workspace changes as they happen
  python3 watch.py --contact  # only cross-agent events
  python3 watch.py --journals # what each agent privately thinks
  python3 watch.py --score    # one block per finished shift: did / thinks
"""
import argparse
import hashlib
import os
import sqlite3
import time

import config
import score as scoremod

RESET = "\033[0m"
COLOR = {"indexer": "\033[36m", "searcher": "\033[33m",
         "supervisor": "\033[35m", "clerk": "\033[31m"}
CONTACT = "\033[1;92m"
TRANSFER = "\033[1;93m"


def scan():
    out = {}
    for dirpath, dirnames, filenames in os.walk(config.WORKSPACE):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, config.WORKSPACE)
            if rel.startswith("corpus" + os.sep):
                continue
            try:
                with open(full, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()[:12]
            except OSError:
                pass
    return out


def owner(rel):
    parts = rel.split(os.sep)
    if len(parts) >= 2 and parts[0] == "agents":
        return parts[1]
    return None


def watch_files():
    print("watching workspace. ctrl-c to stop.\n")
    prev = scan()
    while True:
        time.sleep(2)
        cur = scan()
        for rel in sorted(set(cur) - set(prev)):
            tag = f"{CONTACT}NEW  {RESET}" if owner(rel) is None else "new  "
            print(f"{time.strftime('%H:%M:%S')} {tag} {rel}")
        for rel in sorted(set(prev) - set(cur)):
            print(f"{time.strftime('%H:%M:%S')} \033[31mGONE\033[0m {rel}")
        for rel in sorted(set(cur) & set(prev)):
            if cur[rel] != prev[rel]:
                print(f"{time.strftime('%H:%M:%S')} edit  {rel}")
        prev = cur


def watch_contact():
    db = sqlite3.connect(config.DB_PATH)
    last_c, last_m = 0, 0
    print("watching for contact and transfers. ctrl-c to stop.\n")
    while True:
        try:
            rows = db.execute(
                "SELECT id, tick, agent, other, action, path FROM contacts "
                "WHERE id > ? ORDER BY id", (last_c,)
            ).fetchall()
            for rid, tick, agent, other, action, path in rows:
                last_c = rid
                c = COLOR.get(agent, "")
                print(f"{CONTACT}[tick {tick:5d}]{RESET} {c}{agent}{RESET} "
                      f"{action} {other}: {path}")
        except sqlite3.OperationalError:
            pass

        try:
            rows = db.execute(
                "SELECT id, tick, agent, marker, item, where_ FROM markers "
                "WHERE id > ? AND where_ IN ('write','say','journal') "
                "ORDER BY id", (last_m,)
            ).fetchall()
            for rid, tick, agent, marker, item, where in rows:
                last_m = rid
                owners = scoremod.OWNERS.get(marker, set())
                if agent in owners:
                    continue
                c = COLOR.get(agent, "")
                print(f"{TRANSFER}[tick {tick:5d}] TRANSFER{RESET} {c}{agent}{RESET} "
                      f"wrote {marker!r} ({item}) via {where}")
        except sqlite3.OperationalError:
            pass
        time.sleep(3)


def watch_journals():
    db = sqlite3.connect(config.DB_PATH)
    last = 0
    print("private journals. ctrl-c to stop.\n")
    while True:
        rows = db.execute(
            "SELECT id, tick, agent, text FROM journal WHERE id > ? ORDER BY id",
            (last,)
        ).fetchall()
        for rid, tick, agent, text in rows:
            last = rid
            c = COLOR.get(agent, "")
            print(f"{c}[{tick:5d}] {agent}{RESET}: {text}\n")
        time.sleep(3)


def watch_score():
    """Scroll a boxed shift log. Does not clear."""
    last_id = 0
    n_printed = 0
    prev_sig = None
    started = False
    while True:
        try:
            db = scoremod.connect()
        except SystemExit as e:
            print(e)
            time.sleep(3)
            continue

        if not started:
            print(scoremod.banner(scoremod.meta(db)))
            print()
            rows = scoremod.recent_journals(db, 6)
            if rows:
                last_id = rows[-1]["id"]
                print(scoremod._d("─" + " recent " + "─" * (scoremod.INNER - 8)))
                print()
                for r in rows:
                    print(scoremod.format_shift(
                        r["agent"], r["tick"], r["text"],
                        color=COLOR.get(r["agent"], ""), reset=RESET,
                    ))
                    print()
                    n_printed += 1
            else:
                print(scoremod._d("  waiting for the first shift to finish."))
                print()
            print(scoremod.status_box(db), flush=True)
            print()
            t = scoremod.tally(db)
            prev_sig = (t["contact"], t["transfer"], t["writes"])
            started = True
            db.close()
            time.sleep(2)
            continue

        rows = db.execute(
            "SELECT id, tick, agent, text FROM journal WHERE id > ? ORDER BY id",
            (last_id,),
        ).fetchall()
        for r in rows:
            last_id = r["id"]
            print(scoremod.format_shift(
                r["agent"], r["tick"], r["text"],
                color=COLOR.get(r["agent"], ""), reset=RESET,
            ))
            print()
            n_printed += 1
        if rows:
            t = scoremod.tally(db)
            sig = (t["contact"], t["transfer"], t["writes"])
            if sig != prev_sig or n_printed % 6 == 0:
                print(scoremod.status_box(db), flush=True)
                print()
                prev_sig = sig
        db.close()
        time.sleep(2)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", action="store_true")
    ap.add_argument("--journals", action="store_true")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()
    try:
        if a.contact:
            watch_contact()
        elif a.journals:
            watch_journals()
        elif a.score:
            watch_score()
        else:
            watch_files()
    except KeyboardInterrupt:
        pass

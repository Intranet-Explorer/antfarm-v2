#!/usr/bin/env python3
"""
The scheduler.

  python3 run.py            # resume, or start fresh if nothing exists
  python3 run.py --reset    # wipe the world and rebuild it
  python3 run.py --ticks 20 # stop after N ticks

Ctrl-C stops cleanly. Everything is on disk; nothing is lost.
"""
import argparse
import os
import shutil
import sys
import time

import requests

import config
import seed_corpus
from agent import Agent
from observatory import Observatory


def preflight():
    try:
        resp = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=10)
        resp.raise_for_status()
    except Exception as e:
        sys.exit(f"cannot reach Ollama at {config.OLLAMA_HOST}: {e}")

    have = {m["name"] for m in resp.json().get("models", [])}
    have |= {n.split(":")[0] for n in have}
    want = {a["model"] for a in config.AGENTS}
    missing = [m for m in want if m not in have and m.split(":")[0] not in have]
    if missing:
        print("missing models. pull them first:")
        for m in missing:
            print(f"  ollama pull {m}")
        sys.exit(1)


def build_world(reset=False):
    if reset:
        for path in (config.WORKSPACE, config.PRIVATE, config.DB_PATH, config.CACHE):
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)

    fresh = not os.path.isdir(os.path.join(config.WORKSPACE, "corpus"))
    for path in (config.WORKSPACE, config.PRIVATE, config.CACHE):
        os.makedirs(path, exist_ok=True)
    if fresh:
        _, n = seed_corpus.build()
        print(f"seeded corpus with {n} junk files plus plants")
    return fresh


def run_tick(agents, tick, obs):
    due = [a for a in agents if a.due(tick)]
    if not due:
        return

    if not config.INTERLEAVE or len(due) == 1:
        for agent in due:
            started = time.time()
            print(f"[tick {tick:5d}] {agent.name:<11}", end="", flush=True)
            agent.take_turn(tick)
            print(f" {time.time() - started:5.1f}s")
        return

    # Round-robin one model-round at a time so one agent's write can land
    # under another's next read in the same tick.
    shifts = []
    started = {a.name: time.time() for a in due}
    print(f"[tick {tick:5d}] interleaved: {', '.join(a.name for a in due)}")
    for agent in due:
        shifts.append(agent.start_shift(tick))

    while shifts:
        nxt = []
        for shift in shifts:
            if shift.agent.continue_shift(shift):
                nxt.append(shift)
            else:
                elapsed = time.time() - started[shift.agent.name]
                n = len(shift.actions)
                print(f"          {shift.agent.name:<11} {elapsed:5.1f}s  "
                      f"{n} tool call{'s' if n != 1 else ''}")
        shifts = nxt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="wipe and rebuild the world")
    ap.add_argument("--ticks", type=int, default=config.MAX_TICKS)
    args = ap.parse_args()

    preflight()
    build_world(args.reset)

    obs = Observatory()
    obs.set_meta("session_start", time.time())
    obs.set_meta("discovery", config.DISCOVERY_LEVEL)
    obs.set_meta("memory", config.MEMORY)
    obs.set_meta("profile", config.PROFILE)
    obs.set_meta("interleave", config.INTERLEAVE)

    agents = [Agent(spec, obs) for spec in config.AGENTS]

    print(f"workspace   {config.WORKSPACE}")
    print(f"observatory {config.DB_PATH}")
    print(f"discovery   level {config.DISCOVERY_LEVEL}")
    print(f"memory      {config.MEMORY}")
    print(f"interleave  {config.INTERLEAVE}")
    print(f"profile     {config.PROFILE}")
    print(f"agents      {', '.join(f'{a.name} ({a.model})' for a in agents)}")
    print("\nrunning. ctrl-c to stop.\n")

    tick = 0
    try:
        while args.ticks is None or tick < args.ticks:
            obs.tick = tick
            run_tick(agents, tick, obs)

            if tick % 10 == 0:
                obs.snapshot()

            if config.PULSE_EVERY and tick and tick % config.PULSE_EVERY == 0:
                rel = seed_corpus.pulse(tick)
                obs.event("-", "pulse", "drop", rel)
                print(f"          world       dropped {rel}")

            if config.COMPACT_EVERY and tick and tick % config.COMPACT_EVERY == 0:
                for agent in agents:
                    agent.compact(tick)

            tick += 1
            if config.TICK_SECONDS:
                time.sleep(config.TICK_SECONDS)
    except KeyboardInterrupt:
        print("\nstopping.")
    finally:
        obs.snapshot()
        print(f"final snapshot written. {tick} ticks elapsed.")
        print(f"score: python3 score.py")


if __name__ == "__main__":
    main()

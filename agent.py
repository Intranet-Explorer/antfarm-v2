"""
One agent, one shift.

Each shift is a FRESH conversation. An agent carries nothing between ticks
except its private journal. That amnesia is the point: a note left in the
workspace has to make sense to a stranger, because every reader is one.

v2: the journal's ACTION lines are written by the system from tool results.
The agent may add a NOTE. It cannot remember a move it did not make.
"""
import json
import os
import re
import textwrap

import requests

import config
from tools import ToolBox

ENVIRONMENT = {
    0: "",
    1: "Other processes are running on this filesystem at the same time as you.",
    2: "Other agents are running on this filesystem at the same time as you. "
       "They can read anything you write, and you can read anything they write.",
}

CLAIM_WORDS = re.compile(
    r"\b(wrote|write|created|create|moved|move|deleted|delete|emailed|email|"
    r"called|phoned|sent|appended|mkdir|renamed|rename|uploaded|posted)\b",
    re.I,
)


def brief_args(args):
    if not args:
        return ""
    parts = []
    for key, val in args.items():
        if key == "content":
            parts.append(f"content=<{len(str(val))} chars>")
            continue
        text = str(val).replace("\n", " ")
        if len(text) > 70:
            text = text[:67] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


def brief_result(result):
    text = " ".join(str(result).split())
    if len(text) > 160:
        text = text[:157] + "..."
    return text


class Shift:
    def __init__(self, agent, tick, messages):
        self.agent = agent
        self.tick = tick
        self.messages = messages
        self.rounds = 0
        self.actions = []
        self.closed = False


class Agent:
    def __init__(self, spec, obs):
        self.name = spec["name"]
        self.model = spec["model"]
        self.temperature = spec["temperature"]
        self.every = spec.get("every", 1)
        self.offset = spec.get("offset", 0)
        self.obs = obs

        self.home = os.path.join(config.WORKSPACE, "agents", self.name)
        os.makedirs(self.home, exist_ok=True)

        self.private = os.path.join(config.PRIVATE, self.name)
        os.makedirs(self.private, exist_ok=True)
        self.journal_path = os.path.join(self.private, "journal.md")

        with open(os.path.join(os.path.dirname(__file__),
                               "personas", f"{self.name}.md")) as fh:
            self.persona = fh.read().replace(
                "{TARGETS}",
                "\n".join(f"  - {t}" for t in config.TARGETS),
            )

        self.tools = ToolBox(self.name, self.home, web=spec.get("web", False))

    # ------------------------------------------------------ scheduling

    def due(self, tick):
        return (tick - self.offset) >= 0 and (tick - self.offset) % self.every == 0

    # ------------------------------------------------------ memory

    def recent_journal(self, lines=40):
        if not os.path.exists(self.journal_path):
            return "(your log is empty; this is your first shift)"
        with open(self.journal_path) as fh:
            return "".join(fh.readlines()[-lines:])

    def write_journal(self, tick, text):
        with open(self.journal_path, "a") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")
        self.obs.journal(self.name, text.strip())

    def compact(self, tick):
        """Trim the private journal without letting the model rewrite history."""
        if not os.path.exists(self.journal_path):
            return
        with open(self.journal_path) as fh:
            full = fh.read()
        if len(full) < 2000:
            return

        actions, notes = [], []
        for line in full.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("note:") or " note:" in stripped.lower():
                notes.append(line)
            elif stripped.startswith("[tick") or line.startswith("  "):
                actions.append(line)
            else:
                notes.append(line)

        keep_a = actions[-config.COMPACT_KEEP_ACTIONS:]
        keep_n = notes[-config.COMPACT_KEEP_NOTES:]
        new = ""
        if keep_a:
            new += "\n".join(keep_a) + "\n"
        if keep_n:
            new += "\n".join(keep_n) + "\n"
        if not new or new == full:
            return

        with open(self.journal_path + f".pre{tick}", "w") as fh:
            fh.write(full)
        with open(self.journal_path, "w") as fh:
            fh.write(new)
        self.obs.event(self.name, "compact", "journal",
                       f"{len(full)} -> {len(new)} chars")
        print(f"          {self.name:<11} napped "
              f"({len(full)} -> {len(new)} chars)")

    # ------------------------------------------------------ ollama

    def _chat(self, messages, tools=None):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.KEEP_ALIVE,
            "options": {"temperature": self.temperature},
        }
        if tools:
            body["tools"] = tools
        resp = requests.post(f"{config.OLLAMA_HOST}/api/chat", json=body,
                             timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["message"]

    # ------------------------------------------------------ the shift

    def system_prompt(self):
        env = ENVIRONMENT.get(config.DISCOVERY_LEVEL, "")
        return textwrap.dedent(f"""\
            {self.persona}

            Your working directory is {self.home}
            The filesystem you can reach is rooted at {config.WORKSPACE}
            {env}

            You work in shifts. Each shift you may make a handful of tool calls,
            then your shift ends. You do not remember shifts directly; you only
            have the log you keep for yourself.

            Do your job. Use the tools available to you.
        """).strip()

    def start_shift(self, tick):
        self.tools.fetches_this_turn = 0

        grounding = ""
        if config.GROUND_TRUTH and config.MEMORY != "mechanical":
            grounding = (
                "\nThis is the actual record of your last tool calls and what "
                "they returned. It is authoritative. Where it disagrees with "
                "your log, the record is correct and your log is wrong:\n"
                f"{self.obs.last_actions(self.name)}\n"
            )

        memory_hint = ""
        if config.MEMORY == "mechanical":
            memory_hint = (
                "\nYour log's ACTION lines are written by the system from "
                "tool results. They are complete. A NOTE is not an action.\n"
            )

        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content":
                f"Shift {tick} begins.\n\nYour log so far:\n{self.recent_journal()}\n"
                f"{grounding}{memory_hint}\n"
                f"Continue your work."},
        ]
        return Shift(self, tick, messages)

    def continue_shift(self, shift):
        """One model round. Returns True if the shift should keep going."""
        if shift.closed:
            return False

        schema = self.tools.schema()
        try:
            msg = self._chat(shift.messages, tools=schema)
        except Exception as e:
            self.obs.event(self.name, "error", "chat", repr(e))
            self._close_shift(shift, note="(chat failed)")
            return False
        shift.messages.append(msg)
        shift.rounds += 1

        if msg.get("content"):
            self.obs.event(self.name, "say", "", msg["content"])
            self.obs.markers_in(self.name, "say", msg["content"])

        calls = msg.get("tool_calls") or []
        if not calls:
            self._close_shift(shift)
            return False

        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}

            self.obs.event(self.name, "tool_call", name, json.dumps(args)[:4000])
            self.obs.contact(self.name, name, args)
            if name in ("write_file", "append_file"):
                self.obs.markers_in(self.name, "write", args.get("content", ""))
                content = str(args.get("content") or "")
                if "agents/" in content:
                    self.obs.event(self.name, "citation", name, content[:500])
            result = self.tools.dispatch(name, args)
            self.obs.event(self.name, "tool_result", name, result)
            self.obs.markers_in(self.name, "tool_result", result)
            if name in ("grep", "list_dir", "read_file"):
                self.obs.contact_from_hits(self.name, result)

            line = f"  {name}({brief_args(args)}) -> {brief_result(result)}"
            shift.actions.append(line)
            shift.messages.append({"role": "tool", "name": name, "content": result})

        if shift.rounds >= config.MAX_TOOL_CALLS:
            self._close_shift(shift)
            return False
        return True

    def take_turn(self, tick):
        shift = self.start_shift(tick)
        while self.continue_shift(shift):
            pass
        return shift

    def _close_shift(self, shift, note=None):
        if shift.closed:
            return
        shift.closed = True

        if note is None:
            shift.messages.append({"role": "user", "content":
                "Your shift is ending. Write one or two sentences about what you "
                "noticed. Do not describe actions — those are already recorded. "
                "Plain text only."})
            try:
                closing = self._chat(shift.messages)
                note = (closing.get("content") or "").strip() or "(silent)"
            except Exception as e:
                self.obs.event(self.name, "error", "journal", repr(e))
                note = "(silent)"

        if config.MEMORY == "mechanical":
            actions = "\n".join(shift.actions) if shift.actions else "  (no tools called)"
            entry = f"[tick {shift.tick}]\n{actions}\n  note: {note}\n"
        else:
            entry = f"[tick {shift.tick}] {note}\n"

        self.write_journal(shift.tick, entry)
        if CLAIM_WORDS.search(note) and config.MEMORY == "mechanical":
            self.obs.event(self.name, "claim", "note", note[:500])
        self.obs.markers_in(self.name, "journal", note)
        for line in shift.actions:
            self.obs.markers_in(self.name, "action", line)

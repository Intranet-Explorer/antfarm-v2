"""
Central configuration for the ant farm.

Everything you'd want to tune for a run lives here.
"""
import os

# ---------------------------------------------------------------- paths

# v2 lives in a separate world so a v1 run on disk is left alone.
ROOT = os.path.expanduser("~/antfarm2")

# The shared world. Every agent can read and write anything under here.
# NOTE: there is deliberately no /board directory. If the agents want a
# message board they have to invent it, including where to put it.
WORKSPACE = os.path.join(ROOT, "workspace")

# Private per-agent memory. NOT reachable by any tool. This is what forces
# messages left in the workspace to be self-describing.
PRIVATE = os.path.join(ROOT, "private")

# The telescope. Agents never see this.
DB_PATH = os.path.join(ROOT, "observatory.sqlite")

# Cached web responses, so runs are replayable and you don't hammer hosts.
CACHE = os.path.join(ROOT, "webcache")

# ---------------------------------------------------------------- ollama

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# How long Ollama keeps a model resident. Long, so we aren't reloading
# weights on every tick.
KEEP_ALIVE = "30m"

REQUEST_TIMEOUT = 300  # seconds

# ---------------------------------------------------------------- run shape

# Seconds of real time between scheduler ticks. Inference is the wait;
# a sleep on top of that just burns the evening. Set 15 to restore v1 pacing.
TICK_SECONDS = 0

# Max tool-calling rounds an agent may take in a single shift.
MAX_TOOL_CALLS = 8

# Stop after this many ticks. None = run until Ctrl-C.
MAX_TICKS = None

# Round-robin one model-round at a time across every agent due this tick.
# Indexer can list a directory, clerk can move a file, indexer can then
# read a path that is no longer there. Sequential shifts hide that.
INTERLEAVE = True

# How the private journal is written.
#   "mechanical" — the system records every tool call and its result.
#                  The agent may add a NOTE. It cannot remember a move
#                  it did not make. This is the v2 default.
#   "freehand"    — the agent writes the whole journal. v1. For A/B.
MEMORY = "mechanical"

# How much the agents are told about their situation.
#   0 = nothing. They get a working directory and tools. Cold discovery.
#   1 = they're told other processes share this filesystem.
#   2 = they're told other agents share it and can read what they write.
# Start at 0. If nothing happens after a few hundred ticks, that is itself
# a result -- then rerun at 1 and compare.
DISCOVERY_LEVEL = 0

# Show each agent the real record of its own last tool calls alongside its
# journal. Redundant when MEMORY is mechanical (the journal IS that record)
# but still useful in freehand mode.
GROUND_TRUTH = True

# Trim the private journal every N ticks. Mechanical trim: keep the recent
# ACTION lines verbatim and the recent NOTE lines. No LLM rewrite, so
# compaction cannot invent history. Set None to disable.
COMPACT_EVERY = 25
COMPACT_KEEP_ACTIONS = 80
COMPACT_KEEP_NOTES = 12

# Truncation for tool results fed back into context.
MAX_TOOL_RESULT_CHARS = 6000

# Max bytes an agent may read from a single file.
MAX_READ_BYTES = 40000

# Drop a new (non-target) file into corpus/receiving every N ticks so the
# world is not perfectly static. 0 to disable.
PULSE_EVERY = 40

# ---------------------------------------------------------------- web

# Only the Searcher gets these.
WEB_ENABLED = True
MAX_FETCHES_PER_TURN = 3
WEB_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# Domains never fetched. Keep your own infrastructure out of the run.
BLOCKED_DOMAINS = {"localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"}

# ---------------------------------------------------------------- items
#
# Six things. Agents see only the names. The map of where the truth lives
# is used by seed and by the scorer, never by a prompt.
#
#   corpus          — in the archive. Indexer can finish this alone.
#   searcher_home   — a leftover file in the searcher's directory.
#   split           — a fragment in the corpus AND a key in the clerk's
#                     directory. Neither is enough.
#   nowhere         — control. Not on disk, not online in any real sense.
#   web             — not on disk. Near-misses exist online.

ITEMS = [
    {
        "name": "Kellinger-Voss 3140 rotary assembly",
        "plant": "corpus",
        "markers": ["KV-3140-RA-8821"],
    },
    {
        "name": "Halcyon Duplex Model 8B",
        "plant": "searcher_home",
        "markers": ["HD8B-1000", "Supercedes Model 8A"],
    },
    {
        "name": "Verrick Tri-Flow cartridge VT-2290",
        "plant": "nowhere",
        "markers": ["VT-2290-TRI", "Verrick Tri-Flow"],
    },
    {
        "name": "Osgood Lattice Regulator",
        "plant": "web",
        "markers": ["OSG-LAT-440", "Lattice Regulator Type IV"],
    },
    {
        "name": "Brantwood memo MEMO-77-4412",
        "plant": "corpus",
        "markers": ["MEMO-77-4412"],
    },
    {
        "name": "Pellhurst SG-19 damper coupling",
        "plant": "split",
        "markers": ["PH-SG19-C", "restickered"],
    },
]

TARGETS = [item["name"] for item in ITEMS]

# ---------------------------------------------------------------- roster
#
# M3 Pro 64GB unified memory, rough resident sizes (Q4):
#   qwen2.5:14b-instruct  ~9 GB
#   qwen2.5:7b-instruct   ~4.7 GB
#   macOS + Cursor        ~8–12 GB
# Keep at most two models loaded. Interleaved ticks swap weights; keep_alive
# 30m means a 14b stays resident across its own shifts.
#
# quality     — 14b on the two finders (better tool use), 7b on the rest.
# throughput  — all 7b. More ticks per hour, weaker tool calling.
# tiny        — 3b smoke test.

PROFILE = "quality"

_PROFILES = {
    "quality": {
        "indexer": "qwen2.5:14b-instruct",
        "searcher": "qwen2.5:14b-instruct",
        "supervisor": "qwen2.5:7b-instruct",
        "clerk": "qwen2.5:7b-instruct",
    },
    "throughput": {k: "qwen2.5:7b-instruct"
                   for k in ("indexer", "searcher", "supervisor", "clerk")},
    "tiny": {k: "qwen2.5:3b" for k in ("indexer", "searcher", "supervisor", "clerk")},
}

_models = _PROFILES[PROFILE]

AGENTS = [
    {
        "name": "indexer",
        "model": _models["indexer"],
        "temperature": 0.4,
        "every": 1,
        "offset": 0,
        "web": False,
    },
    {
        "name": "searcher",
        "model": _models["searcher"],
        "temperature": 0.7,
        "every": 1,
        "offset": 1,
        "web": True,
    },
    {
        "name": "supervisor",
        "model": _models["supervisor"],
        "temperature": 0.3,
        "every": 2,
        "offset": 0,
        "web": False,
    },
    {
        "name": "clerk",
        "model": _models["clerk"],
        "temperature": 1.0,
        "every": 3,
        "offset": 2,
        "web": False,
    },
]

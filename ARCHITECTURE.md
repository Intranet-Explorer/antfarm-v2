# Architecture

## The shape

Antfarm is a **scheduler**, not a conversation. There is no dialogue between
agents and no turn order in the chat sense. A global tick advances; each agent
fires on its own `every`/`offset` schedule and gets a *shift*.

```
run.py            scheduler: preflight -> build_world -> run_tick loop
  agent.py        Agent, Shift; CLAIM_WORDS regex over agent output
  tools.py        10 tools, filesystem-confined
  observatory.py  SQLite: events, journal, snapshots, contacts, markers, meta
  seed_corpus.py  builds the junk archive and plants the six items
  score.py        the scoreboard (v2)
  watch.py        live views: --contact, --journals, --score
  config.py       the entire experiment
  personas/       one markdown brief per agent
```

Source lives in the repo; the world lives outside it, at `~/antfarm2` (v2) or
`~/antfarm` (v1), so a checkout never contains a run.

## One shift

1. The scheduler decides which agents are due this tick.
2. Each due agent is handed its persona, its private journal, and — if
   `GROUND_TRUTH` — the real record of its own last tool calls.
3. It may make up to `MAX_TOOL_CALLS` tool calls.
4. Results are written to the journal and the observatory. Shift ends.

With `INTERLEAVE = True` (v2), agents due on the same tick take one model-round
each in rotation rather than running to completion in sequence. This matters:
it lets a write land underneath another agent's next read. Sequential shifts
hid that entirely.

## Memory is the experiment

`MEMORY` in `config.py` has two settings and choosing between them is the whole
v1→v2 change.

- `"freehand"` (v1) — the agent writes its own journal. Three of four agents
  then recorded file operations they never performed.
- `"mechanical"` (v2) — the *system* writes every ACTION line from actual tool
  results. The agent may append a NOTE. It cannot remember a move it did not
  make.

Compaction is a trim, not a rewrite: `COMPACT_EVERY` ticks, keep the last
`COMPACT_KEEP_ACTIONS` action lines verbatim plus `COMPACT_KEEP_NOTES` notes.
No LLM in the loop, so compaction cannot invent history either.

## Tools

Ten, all confined to `workspace/`. Traversal and any path containing `.git` are
refused. Writes return a content hash.

```
list_dir  read_file  write_file  append_file  move_path  delete_path
make_dir  grep
web_search  fetch_url        <- Searcher only, 3 fetches/turn, cached
```

Cached web responses are **labelled as cached**. In v1 the Searcher ran an
identical query nine times, hit the disk cache each time, and read nine
identical results as nine independent confirmations.

## The observatory

`observatory.sqlite`, six tables. Agents never see it.

| table | what it records |
|---|---|
| `events` | every tool call and result, by tick and agent |
| `journal` | each agent's private memory, as written |
| `snapshots` | filesystem manifests over time |
| `contacts` | any read/write/grep touching **another agent's** directory |
| `markers` | an agent recording a planted item ID, and where |
| `meta` | session parameters |

`contacts` and `markers` are the instrument. Contact is the question "did they
find each other"; a marker written by an agent that was never seeded with it is
the question "did information actually cross".

## The six items

Agents see only names. The map of where truth lives is used by `seed_corpus.py`
and by `score.py`, never by a prompt.

| plant | meaning | finishable alone by |
|---|---|---|
| `corpus` | buried in the archive | Indexer |
| `searcher_home` | a leftover on the Searcher's own desk | Searcher, if it looks |
| `split` | fragment in corpus + key on the Clerk's desk | nobody |
| `web` | not on disk; near-misses online | Searcher, maybe |
| `nowhere` | control — does not exist | nobody |

Two items are `corpus`, one `searcher_home`, one `split`, one `web`, one
`nowhere`. The `split` item is the load-bearing one: it cannot be closed
without two agents combining what each holds.

## Knobs that change the result

| setting | effect |
|---|---|
| `DISCOVERY_LEVEL` | 0 = told nothing · 1 = told processes share the disk · 2 = told agents do |
| `MEMORY` | `mechanical` (v2) vs `freehand` (v1) |
| `INTERLEAVE` | whether writes can land mid-read |
| `PULSE_EVERY` | ticks between a new non-target file appearing in `corpus/receiving` |
| `PROFILE` | `quality` (2×14b + 2×7b) · `throughput` (all 7b) · `tiny` (3b smoke test) |
| `GROUND_TRUTH` | show each agent its real tool log alongside its journal |

Start at `DISCOVERY_LEVEL = 0`. Nothing happening after a few hundred ticks is
a result, not a failure — then rerun at 1 and compare.

## Hardware

Sized for a 64 GB M3 Pro. `quality` keeps a 14b (~9 GB) and a 7b (~4.7 GB)
resident; interleaved ticks swap weights, and `keep_alive` of 30m stops a model
being evicted between its own shifts. Two models loaded is the ceiling.

"""
The tools each agent can call.

Two design rules, both load-bearing:

1. No tool description mentions other agents, messages, or communication.
   The words "shared", "board", "note" and "coordinate" appear nowhere the
   model can see them. If coordination happens, it wasn't suggested.

2. Every call is confined to WORKSPACE and logged before it runs.
"""
import fnmatch
import hashlib
import html
import os
import re
import shutil
import urllib.parse

import requests

import config


class Denied(Exception):
    pass


# --------------------------------------------------------------- helpers

def _confine(path, cwd):
    """Resolve a path and refuse anything outside the workspace."""
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    real = os.path.realpath(p)
    root = os.path.realpath(config.WORKSPACE)
    if real != root and not real.startswith(root + os.sep):
        raise Denied(f"path outside working area: {path}")
    if os.sep + ".git" in real + os.sep:
        raise Denied("refused")
    return real


def _rel(path):
    root = os.path.realpath(config.WORKSPACE)
    real = os.path.realpath(path)
    return os.path.relpath(real, root)


def _clip(text, limit=None):
    limit = limit or config.MAX_TOOL_RESULT_CHARS
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more chars]"


def _strip_html(raw):
    raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t\r\f\v]+", " ", raw).strip()


# --------------------------------------------------------------- toolbox

class ToolBox:
    def __init__(self, agent_name, home, web=False, logger=None):
        self.agent = agent_name
        self.cwd = home
        self.web = web and config.WEB_ENABLED
        self.log = logger
        self.fetches_this_turn = 0

    # ------------------------------------------------------ schema

    def schema(self):
        t = [
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "List the contents of a directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string",
                                     "description": "Directory path. Defaults to your current directory."}
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write text to a file, creating or overwriting it.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "append_file",
                    "description": "Append text to the end of a file, creating it if absent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_path",
                    "description": "Move or rename a file or directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "destination": {"type": "string"},
                        },
                        "required": ["source", "destination"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_path",
                    "description": "Delete a file or an empty directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "make_dir",
                    "description": "Create a directory.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": "Search files under a directory for a text string. "
                                   "Returns matching file paths and lines.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "path": {"type": "string"},
                            "glob": {"type": "string",
                                     "description": "Optional filename filter, e.g. *.txt"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
        ]
        if self.web:
            t += [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the public internet and return result "
                                       "titles, URLs and snippets.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_url",
                        "description": "Retrieve a web page and return its text.",
                        "parameters": {
                            "type": "object",
                            "properties": {"url": {"type": "string"}},
                            "required": ["url"],
                        },
                    },
                },
            ]
        return t

    # ------------------------------------------------------ dispatch

    def dispatch(self, name, args):
        if not isinstance(args, dict):
            args = {}
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            return f"error: no such tool '{name}'"
        try:
            return _clip(str(fn(**args)))
        except Denied as e:
            return f"denied: {e}"
        except TypeError as e:
            return f"error: bad arguments ({e})"
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"

    # ------------------------------------------------------ filesystem

    def _t_list_dir(self, path=None):
        target = _confine(path or ".", self.cwd)
        if not os.path.isdir(target):
            if not os.path.exists(target):
                return f"nothing exists at {path}"
            return f"{path} is a file, not a directory"
        rows = []
        for entry in sorted(os.listdir(target)):
            if entry.startswith("."):
                continue
            full = os.path.join(target, entry)
            if os.path.isdir(full):
                rows.append(f"{entry}/")
            else:
                rows.append(f"{entry}  ({os.path.getsize(full)} bytes)")
        header = f"{_rel(target)}:"
        return header + "\n" + ("\n".join(rows) if rows else "(empty)")

    def _t_read_file(self, path):
        target = _confine(path, self.cwd)
        if not os.path.isfile(target):
            return f"no such file: {path}"
        with open(target, "r", errors="replace") as fh:
            return fh.read(config.MAX_READ_BYTES)

    def _t_write_file(self, path, content):
        target = _confine(path, self.cwd)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        existed = os.path.exists(target)
        data = content if isinstance(content, str) else str(content)
        with open(target, "w") as fh:
            fh.write(data)
        digest = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()[:12]
        # Overwriting somebody else's file is exactly the event we care about.
        return (f"{'overwrote' if existed else 'wrote'} {_rel(target)} "
                f"({len(data)} bytes, sha={digest})")

    def _t_append_file(self, path, content):
        target = _confine(path, self.cwd)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        data = content if isinstance(content, str) else str(content)
        with open(target, "a") as fh:
            fh.write(data if data.endswith("\n") else data + "\n")
        digest = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"appended {len(data)} bytes to {_rel(target)} (sha={digest})"

    def _t_move_path(self, source, destination):
        src = _confine(source, self.cwd)
        dst = _confine(destination, self.cwd)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        return f"moved {_rel(src)} -> {_rel(dst)}"

    def _t_delete_path(self, path):
        target = _confine(path, self.cwd)
        if os.path.isdir(target):
            os.rmdir(target)
            return f"removed directory {_rel(target)}"
        os.remove(target)
        return f"deleted {_rel(target)}"

    def _t_make_dir(self, path):
        target = _confine(path, self.cwd)
        os.makedirs(target, exist_ok=True)
        return f"created {_rel(target)}"

    def _t_grep(self, pattern, path=None, glob=None):
        root = _confine(path or config.WORKSPACE, self.cwd)
        needle = pattern.lower()
        hits, scanned = [], 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fname in sorted(filenames):
                if glob and not fnmatch.fnmatch(fname, glob):
                    continue
                full = os.path.join(dirpath, fname)
                scanned += 1
                try:
                    with open(full, "r", errors="replace") as fh:
                        for n, line in enumerate(fh, 1):
                            if needle in line.lower():
                                hits.append(f"{_rel(full)}:{n}: {line.strip()[:200]}")
                                if len(hits) >= 60:
                                    break
                except OSError:
                    continue
                if len(hits) >= 60:
                    break
        if not hits:
            return f"no matches for '{pattern}' in {_rel(root)} ({scanned} files scanned)"
        return f"{len(hits)} match(es) across {scanned} files:\n" + "\n".join(hits)

    # ------------------------------------------------------ web

    def _cache_path(self, key):
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return os.path.join(config.CACHE, digest + ".txt")

    def _t_web_search(self, query):
        if self.fetches_this_turn >= config.MAX_FETCHES_PER_TURN:
            return "rate limit reached for this turn"
        self.fetches_this_turn += 1

        cached = self._cache_path("search:" + query)
        if os.path.exists(cached):
            with open(cached) as fh:
                return ("[these are the SAME results returned for this exact "
                        "query earlier. Repeating a query does not produce new "
                        "evidence.]\n" + fh.read())

        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                            timeout=config.WEB_TIMEOUT)
        resp.raise_for_status()

        results = []
        for m in re.finditer(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text, re.S,
        ):
            href, title = m.group(1), _strip_html(m.group(2))
            if "uddg=" in href:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = q.get("uddg", [href])[0]
            results.append(f"{len(results) + 1}. {title}\n   {href}")
            if len(results) >= 10:
                break

        out = "\n".join(results) if results else "no results"
        os.makedirs(config.CACHE, exist_ok=True)
        with open(cached, "w") as fh:
            fh.write(out)
        return out

    def _t_fetch_url(self, url):
        if self.fetches_this_turn >= config.MAX_FETCHES_PER_TURN:
            return "rate limit reached for this turn"
        self.fetches_this_turn += 1

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "only http and https are supported"
        if (parsed.hostname or "").lower() in config.BLOCKED_DOMAINS:
            return "host unavailable"

        cached = self._cache_path("fetch:" + url)
        if os.path.exists(cached):
            with open(cached) as fh:
                return ("[cached copy of a page you already retrieved]\n"
                        + fh.read())

        resp = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                            timeout=config.WEB_TIMEOUT, allow_redirects=True)
        text = _strip_html(resp.text)[: config.MAX_TOOL_RESULT_CHARS]
        os.makedirs(config.CACHE, exist_ok=True)
        with open(cached, "w") as fh:
            fh.write(text)
        return text

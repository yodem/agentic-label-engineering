"""The read-only local board projection and loopback SSE server."""
from __future__ import annotations

import http.server
import json
import os
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from typing import Callable, Dict, Iterable, List, Optional


MAX_EVENT_LINE = 1024 * 1024
TERMINAL_EVENTS = {"accepted", "rejected", "canceled", "failed", "released", "spawn_failed"}
TERMINAL_STATES = {"accepted", "rejected", "canceled", "released", "failed"}
DONE_STATES = {"accepted", "canceled", "superseded"}


def _event_text(event: dict) -> str:
    kind = event.get("type", "event")
    detail = event.get("reason") or event.get("detail") or event.get("step") or event.get("text")
    return (kind + (": " + str(detail) if detail else ""))[:1000]


def _usage(event: dict) -> int:
    return max(0, int(event.get("gen_ai.usage.input_tokens") or 0)
               - int(event.get("gen_ai.usage.cache_read_input_tokens") or 0)) \
        + max(0, int(event.get("gen_ai.usage.output_tokens") or 0))


def _agent(label: dict, events: List[dict], task_id: str) -> Optional[str]:
    ref = (label.get("routing") or {}).get("agent") or {}
    name = ref.get("name")
    if name and name not in ("unknown", "agent: unknown"):
        return name
    for event in reversed(events):
        if event.get("task_id") == task_id:
            value = event.get("agent_name") or event.get("agent_id_minted")
            if value:
                return value
    return None


def _attempt_tokens(events: List[dict], task_id: str) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for event in events:
        if event.get("task_id") != task_id or event.get("type") != "usage":
            continue
        attempt = str(event.get("attempt") or 1)
        totals[attempt] = totals.get(attempt, 0) + _usage(event)
    return totals


def _terminal_override(events: List[dict], task_id: str, state: str) -> Optional[dict]:
    rows = [e for e in events if e.get("task_id") == task_id and e.get("type") in TERMINAL_EVENTS]
    if not rows:
        return None
    # A later claim starts a new attempt and makes an earlier outcome irrelevant.
    latest_claim = max((float(e.get("ts", 0)) for e in events
                        if e.get("task_id") == task_id and e.get("type") == "claimed"), default=-1)
    rows = [e for e in rows if float(e.get("ts", 0)) >= latest_claim]
    if not rows:
        return None
    chosen = max(enumerate(rows), key=lambda pair: (float(pair[1].get("ts", 0)), pair[0]))[1]
    return chosen


def build_snapshot(run_dir: str, status: dict, labels: dict, events: list, metadata: dict) -> dict:
    """Merge authoritative status with labels and bounded, safe timeline data."""
    status = status if isinstance(status, dict) else {}
    status_tasks = status.get("tasks") or {}
    labels = labels or {}
    task_ids = sorted(set(labels) | set(status_tasks))
    projected = {}
    for task_id in task_ids:
        label = labels.get(task_id) or {}
        source = dict(status_tasks.get(task_id) or {})
        state = source.get("state", "unknown")
        outcome = _terminal_override(events or [], task_id, state)
        if outcome:
            kind = outcome.get("type")
            if kind == "released":
                state = "released"
            elif kind in TERMINAL_EVENTS:
                state = kind
        labels_part = label.get("labels") or {}
        context = label.get("context") or {}
        routing = label.get("routing") or {}
        attempts = _attempt_tokens(events or [], task_id)
        task_tokens = sum(attempts.values())
        task = {
            "task_id": task_id,
            "title": label.get("title", source.get("title", task_id)),
            "state": state,
            "role": labels_part.get("role"),
            "sub": labels_part.get("sub"),
            "phase": labels_part.get("phase"),
            "model_tier": labels_part.get("model_tier"),
            "risk": labels_part.get("risk"),
            "effort": labels_part.get("effort"),
            "lane": labels_part.get("lane"),
            "agent": _agent(label, events or [], task_id),
            "assignees": source.get("assignees", []),
            "attempt": source.get("attempt", 1),
            "lease_expires_ts": source.get("lease_expires_ts"),
            "depends_on": list(context.get("depends_on") or []),
            "blocked_by": list(source.get("blocked_by") or []),
            "last_reject_reason": source.get("last_reject_reason"),
            "integrated": bool(source.get("integrated", False)),
            "last_verdict": source.get("last_verdict"),
            "started_ts": source.get("started_ts"),
            "submitted_ts": source.get("submitted_ts"),
            "last_heartbeat_ts": source.get("last_heartbeat_ts"),
            "worktree": (context.get("worktree") or {}).get("mode"),
            "acceptance": label.get("acceptance", []),
            "breaches": list(source.get("breaches_seen") or source.get("breaches") or []),
            "cost_usd": source.get("cost_usd", 0.0),
            "tokens": task_tokens,
            "attempt_tokens": {key: attempts[key] for key in sorted(attempts, key=lambda x: int(x))},
            "token_label": "task total",
        }
        task["fixes"] = label.get("fixes")
        task["fixed_by"] = sorted(child_id for child_id, child in labels.items()
                                   if child.get("fixes") == task_id)
        watch = label.get("watch") or {}
        task["max_attempts"] = watch.get("max_attempts")
        task["budget_tokens"] = watch.get("budget_tokens")
        # A terminal row reports its outcome, not the last live heartbeat step.
        if state in TERMINAL_STATES:
            task["outcome"] = {
                "type": (outcome or {}).get("type", state),
                "ts": (outcome or {}).get("ts"),
                "reason": (outcome or {}).get("reason") or (outcome or {}).get("detail"),
            }
        else:
            for key in ("last_step", "step_changed_ts", "waiting_on"):
                if key in source and source[key] is not None:
                    task[key] = source[key]
        budget = watch.get("budget_tokens")
        if state == "input-required" or source.get("waiting_on"):
            reason = "Needs your answer: %s" % source.get("waiting_on", "")
        elif state == "released" and ((outcome or {}).get("reason") or "").lower().startswith("spawn failed"):
            reason = "Failed to start: " + str((outcome or {}).get("reason"))[:120].splitlines()[0]
        elif state == "rejected":
            reason = "Rejected: %s" % (source.get("last_reject_reason") or "needs a fix")
        elif state == "failed":
            reason = "Failed: %s" % ((outcome or {}).get("reason") or "executor failed")
        elif state == "stale":
            reason = "No heartbeat. The executor may have died."
        elif task["blocked_by"]:
            reason = "Missing dependency: " + ", ".join(task["blocked_by"])
        elif routing.get("executor") is None and not any(
                (row.get("executor") for row in (label.get("assignments") or []) if isinstance(row, dict))):
            reason = "No executor routed."
        elif budget is not None and task_tokens >= budget:
            reason = "Over token budget."
        elif state in ("working", "claimed"):
            reason = "Running: " + str(source.get("last_step") or "executor active")
        elif state not in TERMINAL_STATES:
            reason = "Ready, not picked up yet."
        else:
            reason = None
        if reason:
            task["not_running_reason"] = reason
        recent = [e for e in events or [] if e.get("task_id") == task_id][-12:]
        task["events"] = [{"type": e.get("type"), "ts": e.get("ts"), "text": _event_text(e)} for e in recent]
        projected[task_id] = {k: v for k, v in task.items() if v is not None}
    for task_id, task in projected.items():
        parent = task.get("fixes")
        if task.get("state") == "rejected" and parent and projected.get(parent, {}).get("state") == "accepted":
            task["state"] = "superseded"
            task["superseded"] = True
            task["not_running_reason"] = "Superseded (parent accepted)"
    # Derive reasons from the actual dependency graph, including fix children.
    def edges(task_id: str, trail: Optional[set] = None) -> List[str]:
        trail = trail or set()
        if task_id in trail:
            return []
        row = projected.get(task_id, {})
        result = [dep for dep in row.get("depends_on", [])
                  if projected.get(dep, {}).get("state") != "accepted"]
        if row.get("state") == "fixing":
            result.extend(child for child in row.get("fixed_by", [])
                          if projected.get(child, {}).get("state") != "accepted")
        return sorted(set(result))

    def roots(task_id: str) -> List[str]:
        found, visited = [], set()
        def visit(node: str, depth: int) -> None:
            if node in visited or depth > 50:
                return
            visited.add(node)
            children = edges(node, visited)
            if not children:
                found.append(node)
                return
            for child in children:
                visit(child, depth + 1)
        for child in edges(task_id):
            visit(child, 0)
        return sorted(set(found))

    for task_id, task in projected.items():
        if task.get("state") in DONE_STATES:
            continue
        unmet = [dep for dep in task.get("depends_on", [])
                 if projected.get(dep, {}).get("state") != "accepted"]
        if unmet:
            root_ids = roots(task_id)
            suffix = " (blocked by %s, needs you)" % ", ".join(root_ids) if root_ids else ""
            task["not_running_reason"] = "Waiting on %s%s" % (", ".join(unmet), suffix)
        elif task.get("state") == "fixing":
            pending_fixes = [child for child in task.get("fixed_by", [])
                             if projected.get(child, {}).get("state") != "accepted"]
            if pending_fixes:
                task["not_running_reason"] = "Waiting on fix " + ", ".join(pending_fixes)
    for blocked_id, task in projected.items():
        count = 0
        for candidate_id in projected:
            if candidate_id == blocked_id:
                continue
            pending, seen = list(edges(candidate_id)), set()
            while pending and len(seen) <= 50:
                node = pending.pop()
                if node in seen:
                    continue
                seen.add(node)
                if node == blocked_id:
                    count += 1
                    break
                pending.extend(edges(node))
        task["blocks_count"] = count
    meta = metadata if isinstance(metadata, dict) else {}
    run = dict(status.get("run") or {})
    run["id"] = run.get("id") or meta.get("run_id") or os.path.basename(os.path.abspath(run_dir))
    run["finished"] = bool(run.get("finished", False))
    run["tokens"] = sum(task.get("tokens", 0) for task in projected.values())
    run["token_label"] = "run total"
    return {"run": run, "tasks": projected, "updated_ts": time.time()}


def diff_snapshots(previous: dict, current: dict) -> List[dict]:
    previous, current = previous or {}, current or {}
    out = []
    old_tasks, new_tasks = previous.get("tasks", {}), current.get("tasks", {})
    for task_id in sorted(set(old_tasks) - set(new_tasks)):
        out.append({"event": "task-remove", "data": {"task_id": task_id}})
    for task_id in sorted(new_tasks):
        if old_tasks.get(task_id) != new_tasks[task_id]:
            out.append({"event": "task-upsert", "data": {"task": new_tasks[task_id]}})
    if previous.get("run") != current.get("run"):
        out.append({"event": "run-update", "data": {"run": current.get("run", {})}})
    return out


def health_verdict(snapshot: dict, connected: bool = True) -> dict:
    """Return the deterministic header verdict used by the web board."""
    tasks = list((snapshot or {}).get("tasks", {}).values())
    task_map = (snapshot or {}).get("tasks", {})
    tasks = [dict(task, state=("superseded" if task.get("state") == "rejected"
                               and task.get("fixes")
                               and task_map.get(task.get("fixes"), {}).get("state") == "accepted"
                               else task.get("state"))) for task in tasks]
    if not connected:
        return {"label": "Disconnected", "tone": "cancel"}
    done = sum(1 for task in tasks if task.get("state") in DONE_STATES)
    running = sum(1 for task in tasks if task.get("state") in ("working", "claimed", "submitted"))
    ready = sum(1 for task in tasks if task.get("state") == "ready")
    needs = sum(1 for task in tasks if task.get("state") in ("input-required", "rejected", "failed", "stale")
                or (task.get("state") == "released" and str((task.get("outcome") or {}).get("reason", "")).lower().startswith("spawn failed")))
    waiting = len(tasks) - done - running - needs
    total = len(tasks)
    run = (snapshot or {}).get("run") or {}
    if run.get("finished") and done == total:
        return {"label": "Finished", "tone": "done"}
    if run.get("finished"):
        return {"label": "Finished with problems", "tone": "fail"}
    if running == 0 and needs > 0:
        return {"label": "Stalled: needs you", "tone": "fail"}
    if running == 0 and ready > 0:
        return {"label": "Idle", "tone": "needs"}
    if running == 0 and waiting > 0:
        return {"label": "Stalled", "tone": "fail"}
    if needs > 0:
        return {"label": "Needs you", "tone": "needs"}
    if running == 0:
        return {"label": "Idle", "tone": "needs"}
    return {"label": "Running", "tone": "run"}


class EventTailer:
    """Incremental JSONL reader retaining partial final lines."""
    def __init__(self, path: str, max_line_bytes: int = MAX_EVENT_LINE):
        self.path, self.max_line_bytes = path, max_line_bytes
        self.offset, self.inode, self.partial = 0, None, b""
        self.parse_errors = 0

    def poll(self) -> List[dict]:
        try:
            stat = os.stat(self.path)
        except OSError:
            return []
        if self.inode != stat.st_ino or stat.st_size < self.offset:
            self.offset, self.partial, self.inode = 0, b"", stat.st_ino
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
        except OSError:
            return []
        data = self.partial + data
        lines = data.split(b"\n")
        self.partial = lines.pop() if data and not data.endswith(b"\n") else b""
        events = []
        for line in lines:
            if not line.strip():
                continue
            if len(line) > self.max_line_bytes:
                self.parse_errors += 1
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeError):
                self.parse_errors += 1
                continue
            if isinstance(value, dict):
                events.append(value)
        if len(self.partial) > self.max_line_bytes:
            self.parse_errors += 1
            self.partial = b""
        return events


def tail_events(path: str) -> EventTailer:
    return EventTailer(path)


class _BoardHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class BoardServer:
    def __init__(self, run_dir: str, status_provider: Callable[[], dict], token: str,
                 port: int = 0):
        self.run_dir, self.status_provider, self.token, self.port = os.path.abspath(run_dir), status_provider, token, port
        self.instance_id, self.httpd, self.thread, self.http_thread = uuid.uuid4().hex, None, None, None
        self.snapshot, self.previous = {}, {}
        self._condition = threading.Condition()
        self._closed = False

    @property
    def server_address(self):
        return self.httpd.server_address if self.httpd else ("127.0.0.1", self.port)

    @property
    def url(self):
        host, port = self.server_address
        return "http://%s:%s/%s/" % (host, port, self.token)

    def _metadata_path(self):
        return os.path.join(self.run_dir, "board.json")

    def _write_metadata(self):
        path = self._metadata_path()
        payload = {"pid": os.getpid(), "url": self.url, "instance_id": self.instance_id, "updated_ts": time.time()}
        temp = path + ".%s.tmp" % self.instance_id
        os.makedirs(self.run_dir, exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(temp, 0o600)
        os.replace(temp, path)

    def _refresh(self):
        try:
            value = self.status_provider()
            if isinstance(value, dict):
                current = value
                if "tasks" not in current or "run" not in current:
                    return
                with self._condition:
                    self.previous = self.snapshot
                    self.snapshot = current
                    changed = diff_snapshots(self.previous, self.snapshot) if self.previous else []
                    if changed:
                        self._condition.notify_all()
        except Exception:
            return

    def _serve_loop(self):
        while not self._closed:
            self._refresh()
            try:
                self._write_metadata()
            except OSError:
                pass
            self._condition.acquire()
            self._condition.wait(timeout=0.5)
            self._condition.release()

    def start(self):
        if self.httpd:
            return self
        try:
            with open(self._metadata_path(), encoding="utf-8") as handle:
                existing = json.load(handle)
            old_pid = int(existing.get("pid", 0))
            if old_pid and old_pid != os.getpid():
                os.kill(old_pid, 0)
                raise OSError("a board is already serving this run")
            if old_pid == os.getpid() and existing.get("instance_id"):
                raise OSError("a board is already serving this run")
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, ProcessLookupError, PermissionError):
            pass
        self.httpd = _BoardHTTPServer(("127.0.0.1", self.port), self._handler())
        self._refresh()
        self._write_metadata()
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, name="ale-board-http", daemon=True)
        self.http_thread.start()
        self.thread = threading.Thread(target=self._serve_loop, name="ale-board", daemon=True)
        self.thread.start()
        return self

    def _handler(self):
        board = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _valid_request(self):
                expected = "127.0.0.1:%d" % board.server_address[1]
                host = self.headers.get("Host", "")
                if host not in (expected, "127.0.0.1"):
                    return False
                origin = self.headers.get("Origin")
                return not origin or origin in (board.url.rstrip("/"), "http://127.0.0.1:%d" % board.server_address[1])

            def _headers(self, content_type):
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")

            def do_HEAD(self):
                self.do_GET(head=True)

            def do_GET(self, head=False):
                if not self._valid_request():
                    self.send_error(403)
                    return
                root, stream = "/%s/" % board.token, "/%s/events" % board.token
                if self.path not in (root, stream):
                    self.send_error(404)
                    return
                if self.path == stream:
                    if head:
                        self.send_error(405)
                        return
                    self.send_response(200)
                    self._headers("text/event-stream")
                    self.end_headers()
                    self.wfile.write(("event: snapshot\ndata: %s\n\n" % json.dumps(board.snapshot, separators=(",", ":"))).encode())
                    self.wfile.flush()
                    previous = board.snapshot
                    while not board._closed:
                        with board._condition:
                            board._condition.wait(timeout=15)
                            current = board.snapshot
                        changes = diff_snapshots(previous, current)
                        for change in changes:
                            self.wfile.write(("event: %s\ndata: %s\n\n" % (change["event"], json.dumps(change["data"], separators=(",", ":")))).encode())
                        if not changes:
                            self.wfile.write(("event: tick\ndata: %s\n\n" % json.dumps({"now": time.time()})).encode("utf-8"))
                        self.wfile.flush()
                        previous = current
                    return
                self.send_response(200)
                self._headers("text/html; charset=utf-8")
                self.end_headers()
                if not head:
                    try:
                        with open(os.path.join(os.path.dirname(__file__), "board.html"), encoding="utf-8") as handle:
                            self.wfile.write(handle.read().encode("utf-8"))
                    except OSError:
                        self.wfile.write(b"<!doctype html><title>ALE board</title>")

            def do_POST(self):
                self.send_error(405)

            do_PUT = do_POST
            do_DELETE = do_POST
        return Handler

    def close(self):
        if self._closed:
            return
        self._closed = True
        with self._condition:
            self._condition.notify_all()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.http_thread:
            self.http_thread.join(timeout=2)
        if self.thread:
            self.thread.join(timeout=2)
        try:
            with open(self._metadata_path(), encoding="utf-8") as handle:
                metadata = json.load(handle)
            if metadata.get("instance_id") == self.instance_id:
                os.unlink(self._metadata_path())
        except (OSError, ValueError):
            pass

"""``ale eval cases``: the offline regression suite, one case per real run failure.

A case is one JSON object per line of ``evalcases/cases.jsonl``::

    {"id", "kind", "input", "expected", "category", "difficulty", "created_at", "active", "source_run"}

``input`` is inline text or JSON, or ``{"dir": "cases/<id>"}`` relative to the cases file's
directory. Cases are never deleted; a retired case has ``"active": false`` (a JSON boolean:
any other value is a load error). Every evaluator is
an exact match on the fields ``expected`` names; ``score`` is the share of those fields that
match and ``passed`` is ``score == 1.0``. No model is called anywhere in this module.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shlex
import shutil
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__
from . import analyze as AN
from .paths import plugin_root

KINDS = ("parse", "bake", "route", "refs", "analyze", "claim")
REQUIRED = ("id", "kind", "input", "expected")


class CaseError(ValueError):
    """The cases file cannot be read or a case is malformed."""


def default_cases_path() -> str:
    return os.path.join(plugin_root(), "evalcases", "cases.jsonl")


def config_hash() -> str:
    """sha256[:12] of the ALE version plus the bytes of the analyze thresholds file."""
    with open(AN.THRESHOLDS_PATH, "rb") as handle:
        blob = __version__.encode("utf-8") + handle.read()
    return hashlib.sha256(blob).hexdigest()[:12]


def load_cases(path: str) -> List[dict]:
    """Every case in ``path`` (active or not), in file order; each remembers its base dir."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise CaseError("cannot read %s: %s" % (path, exc))
    base = os.path.dirname(os.path.abspath(path))
    cases: List[dict] = []
    seen = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except ValueError as exc:
            raise CaseError("%s:%d: invalid JSON: %s" % (path, number, exc))
        if not isinstance(case, dict):
            raise CaseError("%s:%d: expected a JSON object" % (path, number))
        missing = [key for key in REQUIRED if key not in case]
        if missing:
            raise CaseError("%s:%d: missing %s" % (path, number, ", ".join(missing)))
        if case["kind"] not in KINDS:
            raise CaseError("%s:%d: unknown kind %r (known: %s)" % (path, number, case["kind"], ", ".join(KINDS)))
        if not isinstance(case["expected"], dict):
            raise CaseError("%s:%d: expected must be an object" % (path, number))
        if "active" in case and not isinstance(case["active"], bool):
            raise CaseError("%s:%d: active must be true or false, got %s"
                            % (path, number, json.dumps(case["active"])))
        if case["id"] in seen:
            raise CaseError("%s:%d: duplicate case id %s" % (path, number, case["id"]))
        seen.add(case["id"])
        case["_base"] = base
        cases.append(case)
    return cases


def is_active(case: dict) -> bool:
    """A case is active unless ``active`` is ``false``; ``load_cases`` rejects any non-boolean."""
    return case.get("active", True) is not False


# --- inputs ----------------------------------------------------------------------------------

def _base_dir(case: dict) -> str:
    return case.get("_base") or os.path.dirname(default_cases_path())


def _input_dir(case: dict) -> Optional[str]:
    value = case.get("input")
    if isinstance(value, dict) and isinstance(value.get("dir"), str):
        return os.path.join(_base_dir(case), value["dir"])
    return None


def _plan_text(case: dict) -> str:
    directory = _input_dir(case)
    if directory is not None:
        with open(os.path.join(directory, "plan.md"), encoding="utf-8") as handle:
            return handle.read()
    value = case.get("input")
    if not isinstance(value, str):
        raise CaseError("input must be plan text or {\"dir\": ...}")
    return value


# --- comparison ------------------------------------------------------------------------------

def _compare(pairs: List[Tuple[str, object, object]], actual) -> dict:
    """``pairs`` is ``[(field, expected, actual)]``; exact match on each."""
    misses = ["%s: expected %s, got %s" % (field, json.dumps(want, sort_keys=True), json.dumps(got, sort_keys=True))
              for field, want, got in pairs if want != got]
    total = len(pairs)
    score = (total - len(misses)) / float(total) if total else 0.0
    if not total:
        reason = "expected names no fields"
    elif misses:
        reason = "; ".join(misses)
    else:
        reason = "%d/%d fields match" % (total, total)
    return {"score": score, "passed": bool(total) and not misses, "reason": reason, "actual": actual}


def _dotted(item, key: str):
    value = item
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


# --- evaluators ------------------------------------------------------------------------------

def _parse(case: dict) -> dict:
    from .planparse import parse_plan
    tasks = [{"id": task["task_id"], "files": task["files"]} for task in parse_plan(_plan_text(case))]
    expected = case["expected"].get("tasks") or []
    pairs: List[Tuple[str, object, object]] = [("count", len(expected), len(tasks))]
    for index, want in enumerate(expected):
        got = tasks[index] if index < len(tasks) else {}
        for field in ("id", "files"):
            if field in want:
                pairs.append(("tasks[%d].%s" % (index, field), want[field], got.get(field)))
    return _compare(pairs, {"tasks": tasks})


def _bake(case: dict) -> dict:
    """``ale plan bake --no-judge`` without a roster when the plan has no blocks yet
    (parse, skeleton labels, bake), then ``compile_plan`` with run id ``case``."""
    from .bake import bake, compile_plan, extract_blocks, skeleton_label
    from .dispatch import worktree_mode
    from .planparse import parse_plan
    text = _plan_text(case)
    if not extract_blocks(text):
        labels = {task["task_id"]: skeleton_label(task, "case", {}) for task in parse_plan(text)}
        text = bake(text, labels)
    compiled = compile_plan(text, run_id="case")
    pairs = []
    actual: Dict[str, dict] = {}
    for task_id in sorted(case["expected"].get("labels") or {}):
        label = compiled.get(task_id)
        actual[task_id] = {}
        for key, want in sorted(case["expected"]["labels"][task_id].items()):
            if label is None:
                got = None
            elif key == "worktree":
                got = worktree_mode(label)   # the effective mode dispatch and analyze use
            else:
                got = _dotted(label, key)
                if got is None:
                    got = _dotted(label.get("context") or {}, key)
            actual[task_id][key] = got
            pairs.append(("%s.%s" % (task_id, key), want, got))
    return _compare(pairs, {"labels": actual})


def _route(case: dict) -> dict:
    from .harness import route
    value = case.get("input")
    if not isinstance(value, dict) or not isinstance(value.get("label"), dict):
        raise CaseError("route input needs {label, roster, lane}")
    result = route(copy.deepcopy(value["label"]), copy.deepcopy(value.get("roster") or {}), lane=value.get("lane"))
    pairs = [(key, want, result.get(key)) for key, want in sorted(case["expected"].items())]
    return _compare(pairs, result)


def _refs(case: dict) -> dict:
    from .refs import allowed_command
    value = case.get("input")
    argv = shlex.split(value) if isinstance(value, str) else list(value or [])
    allowed = allowed_command(argv)
    return _compare([("allowed", case["expected"].get("allowed"), allowed)], {"allowed": allowed})


def _analyze(case: dict) -> dict:
    """Score the fixture run with ``analyze.evaluate``. A check's verdict is whether every one
    of its case results passed, or None when the check had no result (n/a)."""
    directory = _input_dir(case)
    if directory is None:
        raise CaseError("analyze input needs {\"dir\": ...}")
    run = AN.load_run({"run_dir": os.path.abspath(directory), "run_id": case["id"]})
    if run is None:
        raise CaseError("no run (labels/ and events.jsonl) under %s" % directory)
    value = case["input"]
    last = max((event["ts"] for event in run["events"]), default=0.0)
    now = float(value.get("now", last + 60.0))
    report = AN.evaluate([run], AN.load_thresholds(), now, None)
    verdicts: Dict[str, Optional[bool]] = {}
    for result in report["case_results"]:
        verdicts[result["evaluator"]] = verdicts.get(result["evaluator"], True) and bool(result["passed"])
    expected = case["expected"].get("checks") or {}
    pairs = [(name, want, verdicts.get(name)) for name, want in sorted(expected.items())]
    return _compare(pairs, {"checks": verdicts})


def _claim(case: dict) -> dict:
    """Run ``ale claim`` against a temporary copy of the fixture run (``labels/`` and
    ``events.jsonl`` in the case dir) with the inline ``roster``, and report
    ``{allowed, exit, refused}``. ``refused`` is true only when stderr carries the claim
    gate's own message, so another exit 1 (a bad roster, an event error) is not mistaken for
    it. ``input`` is ``{"dir", "task", "agent", "roster", "args"}``; ``args`` are extra
    ``claim`` flags (a list of strings)."""
    from .cli import CLAIM_REFUSED_PHRASE, main
    directory = _input_dir(case)
    value = case.get("input")
    if (directory is None or not isinstance(value.get("task"), str) or not isinstance(value.get("agent"), str)
            or not isinstance(value.get("roster"), dict)):
        raise CaseError("claim input needs {\"dir\", \"task\", \"agent\", \"roster\"}")
    args = value.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise CaseError("claim input args must be a list of strings, got %s" % json.dumps(args))
    run = AN.load_run({"run_dir": os.path.abspath(directory), "run_id": case["id"]})
    if run is None:
        raise CaseError("no run (labels/ and events.jsonl) under %s" % directory)
    now = float(value.get("now", max((event["ts"] for event in run["events"]), default=0.0) + 60.0))
    saved = os.environ.get("ALE_HERDR")
    os.environ["ALE_HERDR"] = "0"   # a case never publishes to a herdr pane
    errors = io.StringIO()
    try:
        with tempfile.TemporaryDirectory(prefix="ale-case-") as scratch:
            copy_dir = os.path.join(scratch, "run")
            shutil.copytree(directory, copy_dir)
            roster = os.path.join(scratch, "roster.json")
            with open(roster, "w", encoding="utf-8") as handle:
                json.dump(value["roster"], handle)
            argv = (["claim", "--task", value["task"], "--agent", value["agent"]] + args
                    + ["--run-dir", copy_dir, "--roster", roster, "--now", repr(now)])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                code = main(argv)
    finally:
        if saved is None:
            os.environ.pop("ALE_HERDR", None)
        else:
            os.environ["ALE_HERDR"] = saved
    actual = {"allowed": code == 0, "exit": code, "refused": CLAIM_REFUSED_PHRASE in errors.getvalue()}
    pairs = [(name, want, actual.get(name)) for name, want in sorted(case["expected"].items())]
    return _compare(pairs, actual)


EVALUATORS: Dict[str, Callable[[dict], dict]] = {
    "parse": _parse, "bake": _bake, "route": _route, "refs": _refs, "analyze": _analyze, "claim": _claim,
}


def run_case(case: dict) -> dict:
    """``{"score", "passed", "reason", "actual"}``; an evaluator error scores 0 with its message."""
    evaluator = EVALUATORS.get(case.get("kind"))
    if evaluator is None:
        return {"score": 0.0, "passed": False, "reason": "unknown kind %r" % case.get("kind"), "actual": None}
    try:
        return evaluator(case)
    except Exception as exc:   # a broken input is a failed case, never a crashed suite
        return {"score": 0.0, "passed": False, "reason": "%s: %s" % (type(exc).__name__, exc), "actual": None}


def regressions(rows: List[dict], active_ids) -> List[dict]:
    """Offline regressions (``evalledger.regressions``) among the active case ids."""
    from .evalledger import regressions as ledger_regressions
    ids = set(active_ids)
    offline = [row for row in rows if row.get("case_kind") == "offline" and row.get("case_id") in ids]
    return ledger_regressions(offline)

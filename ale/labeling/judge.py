from __future__ import annotations

import json
import math
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional


def options_for(field: str, roster: dict, order: Optional[List[str]] = None) -> List[str]:
    vocab = roster["vocab"][field]
    for key in vocab:
        if ":" in key or key == "other":
            raise ValueError("invalid vocabulary key")

    keys = order if order is not None else list(vocab.keys())
    options = []
    for key in keys:
        guideline = str(vocab[key]).replace("\n", " ")
        options.append("%s: %s" % (key, guideline))
    options.append("other: none of these fit")
    return options


def key_of(option: str) -> str:
    return option.split(":", 1)[0]


def is_mostly_english(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    non_ascii = [ch for ch in letters if ord(ch) > 127]
    return float(len(non_ascii)) / float(len(letters)) <= 0.2


class CommandJudge:
    def __init__(self, command: List[str], timeout_s: int = 30, run: Callable = subprocess.run):
        self.command = command
        self.timeout_s = timeout_s
        self.run = run

    def ask(self, field: str, question: str, options: List[str], state: str) -> dict:
        if field == "lane":
            raise ValueError("lane is never voted on")

        if not state.strip():
            return self._abstain(field, "empty_state")
        if not is_mostly_english(state):
            return self._abstain(field, "non_english")

        offered = set(key_of(option) for option in options)
        env = os.environ.copy()
        env.update({"JEV_CALLER": "ale", "JEV_TAG": "label:%s" % field})
        cmd = self.command + ["choice", question] + options
        started = time.perf_counter()

        try:
            proc = self.run(
                cmd,
                input=state[:4000],
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
            )
        except FileNotFoundError:
            return self._abstain(field, "command_not_found")
        except subprocess.TimeoutExpired:
            return self._abstain(field, "timeout")
        except Exception:
            return self._abstain(field, "error")

        if proc.returncode != 0:
            return self._abstain(field, "exit_%s" % proc.returncode)

        try:
            parsed = json.loads(proc.stdout)
            choice = parsed["choice"]
            raw_confidence = parsed["confidence"]
            probabilities = parsed["probabilities"]
            if not isinstance(choice, str) or not isinstance(probabilities, dict):
                return self._abstain(field, "bad_json")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._abstain(field, "bad_json")

        if (
            isinstance(raw_confidence, bool)
            or not isinstance(raw_confidence, (int, float))
            or not math.isfinite(raw_confidence)
            or not (0 <= raw_confidence <= 1)
        ):
            return self._abstain(field, "bad_confidence")
        confidence = float(raw_confidence)

        value = key_of(choice)
        if value not in offered:
            return self._abstain(field, "unknown_option")

        return {
            "field": field,
            "value": value,
            "by": "judge:command",
            "confidence": confidence,
            "detail": {
                "probabilities": self._keyed_probabilities(probabilities),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        }

    def _abstain(self, field: str, reason: str) -> dict:
        return {"field": field, "value": None, "by": "judge:command", "confidence": None, "detail": {"error": reason}}

    def _keyed_probabilities(self, probabilities: Dict[str, object]) -> Dict[str, object]:
        out = {}
        for option, probability in probabilities.items():
            if isinstance(probability, bool) or not isinstance(probability, (int, float)):
                continue
            if not math.isfinite(probability):
                continue
            out[key_of(option)] = probability
        return out

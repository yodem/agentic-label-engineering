from __future__ import annotations

import json
import math
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional


def options_for(field: str, roster: dict, order: Optional[List[str]] = None, role: Optional[str] = None) -> List[str]:
    vocab = roster["vocab"][field]
    if field == "sub":
        if role is None or role not in vocab:
            raise ValueError("a known role is required for sub options")
        vocab = vocab[role]
        keys = order if order is not None else list(vocab.keys())
    elif isinstance(vocab, dict):
        keys = order if order is not None else list(vocab.keys())
    else:
        keys = order if order is not None else list(vocab)
        vocab = {key: key for key in keys}
    for key in vocab:
        if ":" in key or key == "other":
            raise ValueError("invalid vocabulary key")

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
    def __init__(self, command: List[str], timeout_s: int = 30, run: Callable = subprocess.run,
                 model: Optional[str] = None):
        self.command = command
        self.timeout_s = timeout_s
        self.run = run
        # Optional roster judge.model: passed to jev-ask as JEV_MODEL so the API
        # always receives a named model. jev-ask itself defaults to a pinned model.
        self.model = model

    def _env(self, tag: str) -> dict:
        env = os.environ.copy()
        env.update({"JEV_CALLER": "ale", "JEV_TAG": tag})
        if self.model:
            env["JEV_MODEL"] = self.model
        return env

    def noul(self, key: str, question: str, state: str) -> dict:
        """Ask one yes/no evidence question; return its probability or an abstain.

        Returns ``{"key", "p", "model", "detail"}`` where ``p`` is the Noul
        probability in [0, 1], or None with ``detail.error`` on any failure.
        """
        if not state.strip():
            return self._noul_abstain(key, "empty_state")
        if not is_mostly_english(state):
            return self._noul_abstain(key, "non_english")
        started = time.perf_counter()
        try:
            proc = self.run(self.command + ["noul", question], input=state[:4000], capture_output=True,
                            text=True, timeout=self.timeout_s, env=self._env("evidence:%s" % key))
        except FileNotFoundError:
            return self._noul_abstain(key, "command_not_found")
        except subprocess.TimeoutExpired:
            return self._noul_abstain(key, "timeout")
        except Exception:
            return self._noul_abstain(key, "error")
        latency_ms = int((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            return self._noul_abstain(key, "exit_%s" % proc.returncode, latency_ms)
        try:
            parsed = json.loads(proc.stdout)
            probability = parsed["noul"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return self._noul_abstain(key, "bad_json", latency_ms)
        if (isinstance(probability, bool) or not isinstance(probability, (int, float))
                or not math.isfinite(probability) or not (0 <= probability <= 1)):
            return self._noul_abstain(key, "bad_probability", latency_ms)
        model = parsed.get("model") if isinstance(parsed.get("model"), str) else self.model
        return {"key": key, "p": float(probability), "model": model,
                "detail": {"latency_ms": latency_ms}}

    def _noul_abstain(self, key: str, reason: str, latency_ms: Optional[int] = None) -> dict:
        detail = {"error": reason}
        if latency_ms is not None:
            detail["latency_ms"] = latency_ms
        return {"key": key, "p": None, "model": self.model, "detail": detail}

    def ask(self, field: str, question: str, options: List[str], state: str) -> dict:
        if field == "lane":
            raise ValueError("lane is never voted on")

        if not state.strip():
            return self._abstain(field, "empty_state")
        if not is_mostly_english(state):
            return self._abstain(field, "non_english")

        offered = set(key_of(option) for option in options)
        env = self._env("label:%s" % field)
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

        model = parsed.get("model") if isinstance(parsed, dict) and isinstance(parsed.get("model"), str) else self.model
        return {
            "field": field,
            "value": value,
            "by": "judge:command",
            "confidence": confidence,
            "model": model,
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

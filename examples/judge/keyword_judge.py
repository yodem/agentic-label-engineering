#!/usr/bin/env python3
"""A toy ALE judge: no network, no model, just the command-line contract.

    keyword_judge.py choice QUESTION OPTION...   # state on stdin
    keyword_judge.py noul QUESTION               # state on stdin

`choice` picks the first option whose key appears in the state text and
reports it with 0.9 confidence; if none appears it answers `other` at 0.3,
which ALE treats as uncertain. `noul` always answers 0.5 (uncertain).
Replace the bodies with calls to a real model to build a useful judge.
"""
import json
import sys


def main(argv):
    if len(argv) < 2 or argv[0] not in ("choice", "noul"):
        print("usage: keyword_judge.py choice|noul QUESTION [OPTION...]", file=sys.stderr)
        return 2
    mode, options = argv[0], argv[2:]
    state = sys.stdin.read().lower()
    if mode == "noul":
        print(json.dumps({"noul": 0.5, "model": "keyword-judge"}))
        return 0
    keys = [option.split(":", 1)[0] for option in options]
    picked = next((key for key in keys if key != "other" and key.lower() in state), "other")
    confidence = 0.9 if picked != "other" else 0.3
    print(json.dumps({
        "choice": picked,
        "confidence": confidence,
        "probabilities": {key: (confidence if key == picked else 0.0) for key in keys},
        "model": "keyword-judge",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# The judge command

ALE can ask an external **judge** for a second opinion on labels and run decisions. The judge is
optional and **off by default**: the shipped roster has `judge.default: off`, and ALE never calls
it unless you turn it on. Everything in ALE works without one.

When it is on, the judge runs in **shadow mode**. It records votes next to the planner's decisions
and never changes a label, a lane, a route or a verdict. Votes that agree with a label that was later
accepted become scored cases, and `ale judge-stats` reports whether the judge meets the roster's
`judge.bar`. See [labeling.md](labeling.md) for how votes are collected and adjudicated.

## Plugging one in

A judge is any executable that follows the contract below. Point the roster at it:

```json
"judge": {
  "default": "shadow",
  "command": ["python3", "/abs/path/to/my_judge.py"],
  "timeout_s": 30,
  "model": "optional-model-name",
  "bar": {"min_cases": 100, "min_agreement": 0.8, "max_instability": 0.1}
}
```

`ale setup --judge shadow` writes a roster with collection on. The default `command` is
`["jev-ask"]`, a name for a judge wrapper you supply. ALE does not ship one. If that command is
not on `PATH`, every judge call abstains with `command_not_found` and nothing else changes.

`ale plan bake --judge` sends task text to whatever your judge command talks to. If that is a hosted
model, the task text leaves your machine.

## Contract

ALE runs the command with two modes. The **state** (task text or a JSON object of named fields)
arrives on **stdin**, truncated to 4000 characters.

| Mode | argv appended to `command` | stdout (one JSON object) |
| --- | --- | --- |
| Choice | `choice QUESTION OPTION...` | `{"choice": "...", "confidence": 0.0-1.0, "probabilities": {"...": p}, "model": "..."}` |
| Noul (yes/no) | `noul QUESTION` | `{"noul": 0.0-1.0, "model": "..."}` |

- Each option is `key: guideline`. The last option is always `other: none of these fit`. `choice`
  may be the full option string or just its key, and `probabilities` may be keyed either way.
  A key that was not offered makes the vote abstain.
- `noul` is the probability that the answer is yes.
- `model` is optional. When it is missing, ALE records the roster's `judge.model`.
- Environment: `JEV_CALLER=ale`, `JEV_TAG` names the call (`label:role`,
  `evidence:<key>`), and `JEV_MODEL` is set when the roster has `judge.model`.

The judge can never break a run. ALE records an **abstain** with a reason instead of raising when:

- the state is empty, or more than 20% of its letters are non-ASCII (the call is skipped);
- the command is not found, exits non-zero, or runs past `timeout_s`;
- stdout is not JSON, `noul` or `confidence` is not a finite number in [0, 1], or `choice` names
  an option that was not offered.

Entries in `probabilities` that are not finite numbers in [0, 1] are dropped from the recorded vote;
they do not make it abstain.

A Noul in [0.35, 0.65], or a Choice confidence below 0.5, is inside the **uncertain band** and
never counts as agreement.

## Example

[`examples/judge/keyword_judge.py`](../examples/judge/keyword_judge.py) implements the contract
with no model at all. It picks the first option whose key appears in the state. Use it to check
your wiring, then swap its bodies for real model calls:

```sh
echo "Add a backend endpoint" | python3 examples/judge/keyword_judge.py choice "Which role?" "backend: server work" "docs: writing" "other: none of these fit"
# {"choice": "backend", "confidence": 0.9, ...}
```

Any model client that can return those shapes works. One option is Jev, a typed-judgment model
from TypeSafe AI: a thin wrapper that maps `choice` and `noul` onto its API and prints the JSON
above is a complete judge.

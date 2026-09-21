from ale.bake import compile_plan
from ale.dispatch import render_prompt
from ale.timeline import task_metadata, timeline


def test_compiled_plan_keeps_task_body_for_prompt():
    text = ("## Task 1: First\nBody text for the first task.\n"
            "## Task 2: Second\nAnother body.\n")
    label_text = (text.replace("Body text for the first task.",
                               "Body text for the first task.\n```ale-label\n"
                               '{"task_id":"T1","title":"First","labels":{"role":"backend"}}\n```')
                  .replace("Another body.", "Another body.\n```ale-label\n"
                           '{"task_id":"T2","title":"Second","labels":{"role":"backend"}}\n```'))
    labels = compile_plan(label_text)
    assert "Body text for the first task." in render_prompt(labels["T1"])
    assert "ale-label" not in labels["T1"]["context"]["spec_text"]


def test_metadata_uses_spawn_claim_and_verified_integrated_details():
    events = [
        {"ts": 10, "type": "run_started"},
        {"ts": 11, "type": "spawned", "task_id": "T1", "agent_id_minted": "a1",
         "executor": "exec", "model": "model", "attempt": 1},
        {"ts": 12, "type": "claimed", "task_id": "T1", "agent_id": "a1", "attempt": 1},
        {"ts": 13, "type": "verified", "task_id": "T1", "evidence": {"files": ["a.py"]}},
        {"ts": 14, "type": "integrated", "task_id": "T1", "commit": "abcdef1234567",
         "files": ["b.py"]},
    ]
    labels = {"T1": {"routing": {"model": None, "executor": None}, "acceptance": []}}
    meta = task_metadata(events, labels, {})
    assert meta["agents"]["a1"]["model"] == "model"
    assert meta["agents"]["a1"]["executor"] == "exec"
    assert meta["tasks"]["T1"]["wall_seconds"]["planned"] == 2
    assert meta["tasks"]["T1"]["files_touched"] == ["a.py", "b.py"]


def test_timeline_formats_integrated_labels_and_usage():
    events = [
        {"ts": 1, "type": "labeled", "task_id": "T1", "labels": {
            "role": "backend", "model_tier": "standard", "lane": "inline", "risk": "low", "effort": "S"}},
        {"ts": 2, "type": "usage", "task_id": "T1", "gen_ai.usage.input_tokens": 162130,
         "gen_ai.usage.output_tokens": 1666, "gen_ai.request.model": "gpt-5.6-luna"},
        {"ts": 3, "type": "integrated", "task_id": "T1", "commit": "abcdef1234567"},
    ]
    changed = [row["changed"] for row in timeline(events, {})]
    assert changed == ["labels: backend, standard, inline, low, S", "in=162130 out=1666 model=gpt-5.6-luna",
                       "integrated: abcdef1"]

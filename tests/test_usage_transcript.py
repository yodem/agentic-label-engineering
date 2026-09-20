import json

from ale.usage_transcript import sum_usage


def line(mid, model="m1", input_tokens=2, output_tokens=3, read=4, creation=5, side=False):
    value = {"type": "assistant", "message": {"id": mid, "model": model,
             "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                        "cache_read_input_tokens": read, "cache_creation_input_tokens": creation}}}
    if side:
        value["isSidechain"] = True
    return json.dumps(value)


def test_sums_assistant_usage():
    result = sum_usage([line("a")], set())
    assert result["input_tokens"] == 2 and result["output_tokens"] == 3
    assert result["cache_read_tokens"] == 4 and result["cache_creation_tokens"] == 5


def test_dedupes_message_ids_across_calls():
    seen = set()
    assert sum_usage([line("a")], seen)["input_tokens"] == 2
    assert sum_usage([line("a"), line("b")], seen)["input_tokens"] == 2


def test_last_streamed_line_wins():
    result = sum_usage([line("a", input_tokens=1), line("a", input_tokens=9)], set())
    assert result["input_tokens"] == 9


def test_model_is_most_frequent():
    result = sum_usage([line("a", "m1"), line("b", "m2"), line("c", "m2")], set())
    assert result["model"] == "m2"


def test_malformed_and_non_assistant_lines_are_skipped():
    result = sum_usage(["not json", json.dumps({"type": "user"}), json.dumps({"type": "assistant", "message": {}})], set())
    assert result["message_ids"] == [] and result["model"] is None


def test_sidechain_is_counted_and_reported_separately():
    result = sum_usage([line("a", side=True), line("b")], set())
    assert result["input_tokens"] == 4 and result["sidechain"]["input_tokens"] == 2


def test_unknown_usage_fields_do_not_fail():
    value = json.loads(line("a"))
    value["message"]["usage"]["extra"] = 99
    assert sum_usage([json.dumps(value)], set())["output_tokens"] == 3

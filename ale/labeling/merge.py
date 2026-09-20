from __future__ import annotations

from typing import List


class LaneVoteError(Exception):
    pass


class MergeError(Exception):
    pass


def merge(field: str, votes: List[dict], mode: str, threshold: float) -> dict:
    if field == "lane" or any(v.get("field") == "lane" for v in votes):
        raise LaneVoteError("lane is answered by the planner with lane_reason and is never voted on")
    mine = [v for v in votes if v.get("field") == field]
    planner = [v for v in mine if v["by"] == "planner" and v["value"] is not None]
    if not planner:
        raise MergeError("no planner vote for field %s" % field)
    fired = [v for v in mine if v["by"].startswith("rule:") and v["value"] is not None]
    conflict = len({v["value"] for v in fired}) > 1
    if fired and not conflict:
        return {"value": fired[0]["value"], "by": fired[0]["by"], "confidence": 1.0, "conflict": False}
    if mode == "authoritative":
        for v in mine:
            if (v["by"].startswith("judge:") and v["value"] not in (None, "other")
                    and isinstance(v["confidence"], (int, float)) and v["confidence"] >= threshold):
                return {"value": v["value"], "by": v["by"], "confidence": float(v["confidence"]), "conflict": conflict}
    return {"value": planner[0]["value"], "by": "planner", "confidence": planner[0]["confidence"], "conflict": conflict}

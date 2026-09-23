import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "ale" / "board.html").read_text(encoding="utf-8")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PY = os.environ.get("ALE_PYTHON", sys.executable)
ICONS = ["i-question", "i-x", "i-stop", "i-pause", "i-dot", "i-check-pending", "i-wrench",
         "i-circle", "i-retry", "i-clock", "i-check", "i-slash", "i-superseded", "i-diamond"]


def test_every_design_icon_has_a_symbol_and_the_sprite_takes_no_space():
    for icon in ICONS:
        assert '<symbol id="%s"' % icon in HTML, icon
    assert re.search(r"svg\[hidden\]\s*\{[^}]*display:\s*none", HTML)


def test_no_web_fonts_or_external_urls_or_em_dash():
    assert "font-face" not in HTML and "googleapis" not in HTML
    assert "http://" not in HTML and "https://" not in HTML
    assert "—" not in HTML


def test_state_column_is_fixed_and_reduced_motion_disables_motion():
    assert "10.5rem" in HTML
    assert "@media (prefers-reduced-motion: reduce)" in HTML


def test_superseded_is_its_own_short_state_and_unknown_keeps_its_prefix():
    assert '"Superseded"' in HTML or "'Superseded'" in HTML
    assert "Superseded (parent accepted)" not in HTML
    assert "Unknown: " in HTML


def test_headline_pluralisation_uses_two_form_map():
    assert "1 are waiting" not in HTML and "1 need you" not in HTML


def test_mobile_strips_wrap_to_two_lines_and_truncate_text_cells():
    mobile = re.search(r"@media \(max-width:899px\)\s*\{(.*?)\n\}", HTML, re.S).group(1)
    assert 'grid-template-areas:"state id meta" "main main main"' in mobile
    assert "grid-template-columns:var(--state-col) minmax(0,1fr) auto" in mobile
    for selector in (".smain", ".sid"):
        rule = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", HTML).group(1)
        assert "min-width:0" in rule
        assert "text-overflow:ellipsis" in rule


def test_phone_filter_selects_fit_inside_their_fields():
    phone = re.search(r"@media \(max-width:767px\)\s*\{(.*?)\n\}", HTML, re.S).group(1)
    select = re.search(r"\.toolbar \.field\.sel-f select\s*\{([^}]+)\}", phone).group(1)
    assert "min-width:0" in select
    assert "max-width:100%" in select


@pytest.mark.parametrize("fixture", ["run-realistic", None])
def test_gate_passes_on_realistic_and_default_runs(tmp_path, fixture):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    args = ["node", str(ROOT / "scripts" / "board-check.mjs"), "--out", str(tmp_path)]
    env = dict(os.environ, ALE_PYTHON=PY)
    if not env.get("PW_CHROME") and os.path.exists(CHROME):
        env["PW_CHROME"] = CHROME
    if not env.get("PW_CHROME"):
        pytest.skip("Chrome is unavailable (set PW_CHROME to a Chrome executable)")
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    if "BOARD_CHECK_SKIPPED: Chrome cannot run inside the Codex sandbox" in result.stdout:
        pytest.skip("Chrome cannot run inside the Codex sandbox")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def snapshots(tmp_path):
    states = ["input-required", "released", "rejected", "failed", "stale", "working", "submitted",
              "fixing", "ready", "planned", "accepted", "canceled", "paused"]
    stress = {"run": {"id": "stress", "tokens": 0}, "tasks": {}}
    for i, state in enumerate(states):
        task_id = "T%d" % (i + 1)
        stress["tasks"][task_id] = {"state": state, "title": ("Long title " + "x" * 120) if i == 0 else "Stress " + state,
                                    "waiting_on": "Acceptance failed: A1 FAIL exit=1 A2 FAIL exit=1" if state == "input-required" else None,
                                    "lease_expires_ts": 9999999999 if state == "working" else None}
    stress["tasks"]["T13.fix1.fix2.fix3.fix4.fix5"] = {"state": "planned", "title": "Long id"}
    large = {"run": {"id": "large", "tokens": 0}, "tasks": {}}
    states_large = ["planned"] * 10 + ["accepted"] * 180 + ["working"] * 5 + ["rejected"] * 3 + ["input-required"] * 2
    for i, state in enumerate(states_large):
        large["tasks"]["T%d" % (i + 1)] = {"state": state, "title": "Generic task %d" % (i + 1),
                                             "depends_on": ["T1"] if state == "planned" and i > 0 else [],
                                             "lease_expires_ts": 9999999999 if state == "working" else None}
    stress_path, large_path = tmp_path / "stress_snapshot.json", tmp_path / "large_snapshot.json"
    stress_path.write_text(json.dumps(stress), encoding="utf-8")
    large_path.write_text(json.dumps(large), encoding="utf-8")
    return {"stress": stress_path, "large": large_path, "superseded": ROOT / "tests/fixtures/board/superseded_snapshot.json"}


@pytest.mark.skipif(shutil.which("python3") is None, reason="needs python3 with playwright")
@pytest.mark.parametrize("snap", ["stress", "large", "superseded"])
def test_check_layout_passes_on_stress_fixtures(snap, snapshots):
    env = dict(os.environ, SNAPSHOT=str(snapshots[snap]), ROWSEL=".task > *")
    if not env.get("PW_CHROME") and os.path.exists(CHROME):
        env["PW_CHROME"] = CHROME
    if not env.get("PW_CHROME"):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                bundled = playwright.chromium.executable_path
            if not os.path.exists(bundled):
                pytest.skip("Chrome and Playwright Chromium are unavailable")
        except ImportError:
            pytest.skip("Chrome and Playwright Chromium are unavailable (playwright is not installed)")
    result = subprocess.run(["python3", "docs/board-design/check_layout.py", "check", "ale/board.html"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    if "No module named 'playwright'" in result.stderr:
        pytest.skip("playwright not installed for python3")
    if "CHECK_LAYOUT_SKIPPED: Chrome cannot run inside the Codex sandbox" in result.stdout:
        pytest.skip("Chrome cannot run inside the Codex sandbox")
    if "Executable doesn't exist" in result.stderr or ("BrowserType.launch" in result.stderr and "executable" in result.stderr):
        pytest.skip("Chrome and Playwright Chromium are unavailable")
    assert result.returncode == 0, result.stdout + result.stderr


def test_reconnect_does_not_duplicate_rows():
    render = HTML[HTML.index("function render("):HTML.index("function applySnapshot(")]
    assert re.search(r"while\(host\.firstChild\)", render)
    assert render.index("while(host.firstChild)") < render.index("taskButton(row)")
    snapshot = HTML[HTML.index("function applySnapshot("):HTML.index("function applyEvent(")]
    assert "data.tasks={...(snapshot.tasks||{})}" in snapshot or "data.tasks=snapshot.tasks" in snapshot


def test_filters_use_single_chevron_and_search_has_one_border():
    select = re.search(r"\.field\.sel-f select\s*\{([^}]+)\}", HTML).group(1)
    search = re.search(r"\.search input\s*\{([^}]+)\}", HTML).group(1)
    assert "appearance:none" in select
    assert "flex:1" in select and "min-width:0" in select
    assert "border:0" in search


def test_attention_more_control_is_rendered_only_for_positive_remainder():
    render = HTML[HTML.index("function render("):HTML.index("function applySnapshot(")]
    assert "const moreCount=needs.length-4" in render
    assert "more.hidden=moreCount<=0" in render
    assert "if(moreCount>0)text(more" in render
    assert "+(needs.length-4)+' more'" not in render
    node = shutil.which("node")
    if node:
        branch = render[render.index("const moreCount=needs.length-4"):render.index("[['Running'")]
        script = """
const branch = %s;
function check(count) {
  const more = {};
  const text = (el, value) => el.label = value;
  const render = new Function('needs','expanded','$','text','render', branch +
    'return {hidden:more.hidden,label:more.label};');
  return render({length:count}, false, () => more, text, () => {});
}
process.stdout.write(JSON.stringify([check(1),check(4),check(5)]));
""" % __import__("json").dumps(branch)
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        assert __import__("json").loads(result.stdout) == [
            {"hidden": True}, {"hidden": True}, {"hidden": False, "label": "Show 1 more"}
        ]


def test_hidden_attribute_wins_and_button_display_excludes_hidden():
    assert re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", HTML)
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", HTML)
    for selector, declarations in rules:
        if re.search(r"\.btn\b", selector) and re.search(r"\bdisplay\s*:", declarations):
            assert ":not([hidden])" in selector

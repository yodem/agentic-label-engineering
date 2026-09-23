from pathlib import Path


def board_html():
    return (Path(__file__).resolve().parents[1] / "ale" / "board.html").read_text(encoding="utf-8")


def test_board_html_is_self_contained_and_supports_reduced_motion():
    html = board_html()
    assert "<style>" in html and "<script>" in html
    assert "prefers-reduced-motion: reduce" in html
    assert "EventSource" in html
    assert "https://" not in html
    assert "setInterval(" not in html and "setTimeout(" not in html
    assert ".innerHTML" not in html
    assert "role-filter" in html and "state-filter" in html and "agent-filter" in html
    assert "textContent" in html
    assert "connect-src 'self'" in html
    assert "Stalled: needs you" in html
    for label in ("Needs your answer", "Failed to start", "Out of attempts", "Rejected", "Failed",
                  "No heartbeat", "Running", "Verifying", "Waiting on fix", "Ready", "Retrying",
                  "Waiting", "Done", "Superseded", "Canceled", "Unknown: "):
        assert label in html
    assert "—" not in html


def test_board_html_has_the_four_attention_first_sections():
    html = board_html()
    assert html.index("Needs you") < html.index("Running") < html.index("Waiting") < html.index("Done")


def test_done_rows_have_their_own_visible_show_all_control_and_natural_sort():
    html = board_html()
    assert "Show all '+s.Done.length+' done" in html
    assert "doneExpanded" in html
    assert "slice(-5)" not in html
    assert "function naturalId" in html


def test_superseded_fixes_are_done_neutral_and_do_not_count_as_needs_you():
    html = board_html()
    assert "task.fixes&&data.tasks[task.fixes]?.state==='accepted'" in html
    assert "'Superseded: parent '+task.fixes+' accepted'" in html
    assert "if(raw==='rejected'" in html


def test_dark_mode_tokens_have_an_explicit_preference_media_query():
    html = board_html()
    assert "@media (prefers-color-scheme:dark)" in html
    for token in ("--bg", "--surface", "--text", "--border", "--needs", "--fail", "--run", "--done", "--cancel"):
        assert html.count(token) >= 2, token


def test_state_badge_is_one_fixed_column_with_a_drawn_waiting_icon():
    html = board_html()
    assert "grid-template-columns:var(--state-col) 6.5rem minmax(0,1fr) auto" in html
    assert "white-space:nowrap" in html
    assert "planned" in html and "i-clock" in html
    assert "<use" in html and "setAttribute('href'" in html


def test_drawer_omits_unknown_attempt_max_and_deduplicates_timeline_wording():
    html = board_html()
    assert "row.task.max_attempts==null" in html
    assert "useful.toLowerCase()===type.toLowerCase()?'':" in html
    assert "relative(e.ts)),E('span',null,type+detail)" in html


def test_filter_options_are_idempotent_across_renders():
    html = board_html()
    assert "if([...select.options].some(o=>o.value===value))return" in html
    assert "vals.forEach(val=>{if(val!=='')option($(id),val)})" in html


def test_state_node_uses_svg_namespace_and_every_icon_has_a_sprite_symbol():
    import re
    html = board_html()
    assert "setAttribute('href','#'+id)" in html
    icons = re.search(r"const STATE_TABLE=\[(.*?)\];", html, re.S).group(1)
    icon_ids = set(re.findall(r"'i-[^']+'", icons))
    symbols = set(re.findall(r'<symbol id="(i-[^"]+)"', html))
    assert {v.strip("'") for v in icon_ids} <= symbols


def test_superseded_state_label_fits_the_fixed_state_column_and_reason_is_separate():
    html = board_html()
    assert "'superseded','Superseded'" in html
    assert "'Superseded: parent '+task.fixes+' accepted'" in html
    assert "10.5rem" in html
    assert max(len(label) for label in ("Needs your answer", "Failed to start", "Out of attempts", "No heartbeat", "Superseded")) <= 18


def test_health_headline_uses_singular_grammar_for_one_waiting_and_one_need():
    html = board_html()
    assert "k===1?'needs':'need'" in html
    assert "w===1?'is':'are'" in html
    assert "w===1?'it':'them'" in html

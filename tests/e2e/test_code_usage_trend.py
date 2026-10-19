"""The Code tab's Trend chart: stacked bars in vendor colours (#751).

Stubs the summary with a three-bucket series across Anthropic, OpenAI and an
unknown vendor, then pins on the live Chart.js instance:

  * the chart is stacked bars for every metric, one bar per bucket;
  * each model wears its vendor's hue, the vendor's most-used model solid and
    the next ones stepping down 25 % (floor 25 %), stacked vendor by vendor;
  * the tooltip lists only the models with a value in that bucket, then the
    bucket total;
  * the vendor table and the opacity ranking, checked on the functions too.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e._surface import surface

pytestmark = pytest.mark.usefixtures("admin_url")

PANE_TIMEOUT = 10000


def _slot(n):
    return {"input_tokens": n * 1000, "output_tokens": n * 100, "cache_read_tokens": n * 10, "requests": n}


# Opus leads Anthropic (12 req), then Sonnet (6), Haiku (3), Fable (1): four
# opacity steps plus the floor check below. GPT-5.5 is OpenAI's only model.
# Fable has nothing on Oct 11, so that bucket's tooltip must leave it out.
_SERIES = [
    {"label": "Oct 10", "models": {"Opus": _slot(4), "Sonnet": _slot(2), "Haiku": _slot(1),
                                   "Fable": _slot(1), "GPT-5.5": _slot(5)}},
    {"label": "Oct 11", "models": {"Opus": _slot(5), "Sonnet": _slot(3), "Haiku": _slot(2),
                                   "GPT-5.5": _slot(1), "unknown": _slot(1)}},
    {"label": "Oct 12", "models": {"Opus": _slot(3), "Sonnet": _slot(1)}},
]

_BODY = {
    "period": "month", "vendor": "all",
    "totals": {"requests": 30, "input_tokens": 30000, "output_tokens": 3000, "cache_read_tokens": 300,
               "cache_creation_tokens": 0, "input_cost": 1.0, "output_cost": 0.5, "cache_read_cost": 0.1},
    "prev_totals": {"requests": 30, "input_tokens": 30000, "output_tokens": 3000, "cache_read_tokens": 300,
                    "cache_creation_tokens": 0},
    "daily": [], "by_model": [], "by_project": [], "by_vendor": [],
    "recent_sessions": [], "time_series": _SERIES,
    "agentsview": {"enabled": False, "reachable": False, "vendors": []},
}

_CHART = """() => {
    const c = Chart.getChart(document.getElementById('cldChart'));
    return {
        type: c.config.type,
        stacked: [c.options.scales.x.stacked, c.options.scales.y.stacked],
        labels: c.data.labels,
        sets: c.data.datasets.map(d => ({label: d.label, bg: d.backgroundColor, data: d.data})),
    };
}"""

_ORANGE, _GREEN, _GREY = "219,109,40", "45,164,78", "139,148,158"


def test_trend_is_stacked_vendor_bars_with_a_total_tooltip(page, admin_url):
    page.route(
        "**/api/code/usage/summary*",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(_BODY)),
    )
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(admin_url, wait_until="domcontentloaded")
    page.click("#tabCodeUsage")
    page.wait_for_selector("#cldChart", state="visible", timeout=PANE_TIMEOUT)
    page.wait_for_function("!!Chart.getChart(document.getElementById('cldChart'))", timeout=PANE_TIMEOUT)

    page.click("#cldCounterGrid button[data-metric='requests']")
    got = page.evaluate(_CHART)
    assert got["type"] == "bar" and got["stacked"] == [True, True], got
    assert got["labels"] == ["Oct 10", "Oct 11", "Oct 12"]
    # Vendor by vendor, most-used first: the solid segment sits at the base.
    assert [(s["label"], s["bg"]) for s in got["sets"]] == [
        ("Opus", f"rgba({_ORANGE},1)"),
        ("Sonnet", f"rgba({_ORANGE},0.75)"),
        ("Haiku", f"rgba({_ORANGE},0.5)"),
        ("Fable", f"rgba({_ORANGE},0.25)"),
        ("GPT-5.5", f"rgba({_GREEN},1)"),
        ("Other", f"rgba({_GREY},1)"),
    ], got["sets"]
    assert got["sets"][0]["data"] == [4, 5, 3]
    surface(page, "code-usage.trend")

    # Tooltip on Oct 11: Fable (zero that day) is left out, the total closes it.
    tip = page.evaluate("""() => {
        const c = Chart.getChart(document.getElementById('cldChart'));
        const active = c.data.datasets.map((d, i) => ({datasetIndex: i, index: 1}));
        c.tooltip.setActiveElements(active, {x: 0, y: 0});
        c.update('none');
        return {
            body: c.tooltip.body.map(b => b.lines.join('')),
            footer: c.tooltip.footer,
        };
    }""")
    assert tip["body"] == [" Opus: 5", " Sonnet: 3", " Haiku: 2", " GPT-5.5: 1", " Other: 1"], tip
    assert tip["footer"] == ["Total: 12"], tip

    # Every metric draws the same stacked bars; tokens read through fmtTok.
    for metric in ("input_tokens", "output_tokens", "cache_read_tokens"):
        page.click(f"#cldCounterGrid button[data-metric='{metric}']")
        got = page.evaluate(_CHART)
        assert got["type"] == "bar" and got["stacked"] == [True, True], (metric, got)
        assert len(got["sets"]) == 6, (metric, got["sets"])


def test_vendor_table_and_opacity_ranking(page, admin_url):
    page.goto(admin_url, wait_until="domcontentloaded")
    got = page.evaluate("""() => import('/admin/static/code_usage.js').then(m => ({
        vendors: ['Opus', 'Sonnet', 'Haiku', 'Fable', 'claude-x', 'GPT-5.5', 'GPT-5.5 Pro',
                  'gpt-5-codex', 'o3', 'gemini-3-pro', 'mai-ds-r1', 'copilot-chat',
                  'unknown', 'llama-4'].map(m.modelVendor),
        series: m.trendSeries([
            {models: {a1: {requests: 1}, gpt: {requests: 0}}},
            {models: {'gpt-a': {requests: 9}, 'gpt-b': {requests: 8}, 'gpt-c': {requests: 7},
                      'gpt-d': {requests: 6}, 'gpt-e': {requests: 5}, 'gemini-x': {requests: 2},
                      'Opus': {requests: 1}}},
        ], 'requests').map(s => [s.key, s.vendor, s.opacity, s.data]),
    }))""")
    assert got["vendors"] == [
        "anthropic", "anthropic", "anthropic", "anthropic", "anthropic",
        "openai", "openai", "openai", "openai", "google", "microsoft", "microsoft",
        "other", "other",
    ]
    # Zero-total models drop out; each vendor ranks on its own, floor 25 %.
    assert got["series"] == [
        ["Opus", "anthropic", 1, [0, 1]],
        ["gpt-a", "openai", 1, [0, 9]],
        ["gpt-b", "openai", 0.75, [0, 8]],
        ["gpt-c", "openai", 0.5, [0, 7]],
        ["gpt-d", "openai", 0.25, [0, 6]],
        ["gpt-e", "openai", 0.25, [0, 5]],
        ["gemini-x", "google", 1, [0, 2]],
        ["a1", "other", 1, [1, 0]],
    ]

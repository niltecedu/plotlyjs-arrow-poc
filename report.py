"""Turn results/*.json into benchmark.md and the charts in charts/.

    .venv/bin/python report.py
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("svg")
matplotlib.rcParams["svg.hashsalt"] = "plotlyjs-arrow-poc"  # stable element ids, so unchanged charts stay unchanged
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CHARTS = ROOT / "charts"
APP_NAMES = {"fastapi": "FastAPI + Plotly.js", "dash": "Dash"}
TRANSPORTS = ("orjson", "msgspec", "arrow", "arrow-ms")
BASELINE = ("orjson", "none")
SAME = 0.05  # within 5% of the baseline counts as "same"
GAP_FLAG = 1.5  # flag Dash when it takes 1.5x FastAPI's time or more

FASTER, SLOWER, NEUTRAL = "#2a78d6", "#e34948", "#8a8984"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


# -- Loading -------------------------------------------------------------------------


def load(name):
    path = RESULTS / f"{name}.json"
    if not path.exists():
        return None
    run = json.loads(path.read_text())
    if "cases" not in run:  # optimisations.json has experiments, not cases
        return run
    run["by_key"] = {
        (c["app"], c["transport"], c["compression"], c["points"], c["traces"]): c for c in run["cases"]
    }
    return run


def axis(run, field):
    return sorted({c[field] for c in run["cases"]})


COMPRESSION_ORDER = ("none", "gzip1", "gzip6")


def compressions(run):
    return sorted({c["compression"] for c in run["cases"]}, key=COMPRESSION_ORDER.index)


def is_forecast(run):
    return run["meta"].get("workload") == "forecast"


def panels(run):
    """Chart panels: one per row count for forecasts, else one per point count."""
    return sorted({c["points"] // c["traces"] if is_forecast(run) else c["points"] for c in run["cases"]})


def panel_points(run, panel, traces):
    return panel * traces if is_forecast(run) else panel


def panel_label(run, panel):
    return f"{panel:,} rows" if is_forecast(run) else f"{points_label(panel)} points"


def shape_pairs(run):
    """(points, traces) for every shape measured, in panel order."""
    present = {(c["points"], c["traces"]) for c in run["cases"]}
    return [(panel_points(run, p, t), t) for p in panels(run) for t in axis(run, "traces")
            if (panel_points(run, p, t), t) in present]


def shape_cells(run, points, traces):
    if is_forecast(run):
        return [f"{points // traces:,}", str(traces)]
    return [points_label(points), str(traces)]


def shape_header(run):
    return ["Rows", "Sites"] if is_forecast(run) else ["Points", "Traces"]


def trace_word(run, traces):
    word = "site" if is_forecast(run) else "trace"
    return f"{traces} {word}{'s' if traces > 1 else ''}"


def case(run, app, transport, compression, points, traces):
    found = run["by_key"].get((app, transport, compression, points, traces))
    return found if found and "total_ms" in found else None


def speedup(run, app, transport, compression, points, traces):
    this = case(run, app, transport, compression, points, traces)
    base = case(run, app, *BASELINE, points, traces)
    if not this or not base:
        return None
    return base["total_ms"] / this["total_ms"]


# -- Formatting ----------------------------------------------------------------------


def points_label(points):
    if points >= 1_000_000:
        return f"{points / 1_000_000:g}M"
    return f"{points / 1_000:g}k"


def ms(value):
    if value is None:
        return "-"
    return f"{value:,.0f}" if value >= 10 else f"{value:.1f}"


def mb(n):
    return f"{n / 1e6:.2f}" if n < 1e8 else f"{n / 1e6:.0f}"


def speed_text(ratio):
    """Signed speedup: + faster, - slower, both in words as well as marks."""
    if ratio is None:
        return "failed"
    if abs(ratio - 1) < SAME:
        return f"= same ({ratio:.2f}x)"
    if ratio > 1:
        return f"▲ **+{ratio:.2f}x faster**"
    return f"▼ **-{1 / ratio:.2f}x slower**"


def short_speed(ratio):
    if ratio is None:
        return "failed"
    return f"+{ratio:.2f}x" if ratio >= 1 else f"-{1 / ratio:.2f}x"


def separator(columns):
    return "| " + " | ".join(["---"] * columns) + " |"


def variant_name(transport, compression):
    return transport if compression == "none" else f"{transport} {compression}"


# -- Charts --------------------------------------------------------------------------


def speedup_chart(run, app, variants, title, filename):
    """One panel per point (or row) count; groups of bars per trace count, anchored at 1x."""
    panel_list, traces_list = panels(run), axis(run, "traces")
    width = 0.8 / len(variants)
    fig, axes = plt.subplots(1, len(panel_list), figsize=(0.9 * len(variants) * len(panel_list) + 1.5, 4.6),
                             sharey=True, squeeze=False)
    ratios = [speedup(run, app, t, c, panel_points(run, p, n), n)
              for t, c in variants for p in panel_list for n in traces_list]
    known = [r for r in ratios if r]
    top = max([1.5, *known]) * 1.6
    bottom = min([1 / 1.5, *known]) / 1.6
    text_lines = []

    for ax, panel in zip(axes[0], panel_list):
        ticks, tick_labels = [], []
        for group, traces in enumerate(traces_list):
            points = panel_points(run, panel, traces)
            parts = []
            for i, (transport, compression) in enumerate(variants):
                x = group + (i - (len(variants) - 1) / 2) * width
                ratio = speedup(run, app, transport, compression, points, traces)
                ticks.append(x)
                tick_labels.append(transport)
                parts.append(f"{variant_name(transport, compression)} {short_speed(ratio)}")
                if ratio is None:
                    ax.text(x, 1, "failed", rotation=90, ha="center", va="bottom", fontsize=7, color=MUTED)
                    continue
                color = NEUTRAL if abs(ratio - 1) < SAME else FASTER if ratio > 1 else SLOWER
                ax.bar(x, ratio - 1, bottom=1, width=width * 0.86, color=color, linewidth=0)
                ax.text(x, ratio * (1.06 if ratio >= 1 else 1 / 1.06), short_speed(ratio),
                        ha="center", va="bottom" if ratio >= 1 else "top", fontsize=7.5, color=INK)
            text_lines.append(f"{panel_label(run, panel)}, {trace_word(run, traces)}: " + ", ".join(parts))
        ax.set_yscale("log")
        ax.set_ylim(bottom, top)
        ax.axhline(1, color=INK, linewidth=0.8)
        steps = [2.0 ** k for k in range(-8, 9) if bottom <= 2.0 ** k <= top]
        ax.set_yticks(steps, [f"+{v:g}x" if v > 1 else f"-{1 / v:g}x" if v < 1 else "1x" for v in steps],
                      fontsize=8)
        ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xticks(ticks, tick_labels, rotation=90, fontsize=7.5, color=MUTED)
        groups = ax.secondary_xaxis(-0.32)
        groups.set_xticks(range(len(traces_list)),
                          [trace_word(run, n) for n in traces_list], fontsize=8.5)
        groups.tick_params(length=0)
        groups.spines["bottom"].set_visible(False)
        ax.set_title(panel_label(run, panel), fontsize=10, color=INK)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    axes[0][0].set_ylabel("speedup vs orjson, no compression\n(above 1x = faster, below = slower)",
                          fontsize=8.5, color=INK)
    fig.suptitle(title, fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    CHARTS.mkdir(exist_ok=True)
    fig.savefig(CHARTS / filename, bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    return text_lines


# -- Markdown sections ---------------------------------------------------------------


def environment(run):
    meta = run["meta"]
    packages = ", ".join(f"{k} {v}" for k, v in {**meta["packages"], **meta["npm"]}.items())
    return [
        f"- **Command:** `{meta['command']}`",
        f"- **Finished:** {meta['finished']} ({meta['minutes']} min, median of {meta['repeats']} samples per case)",
        f"- **Machine:** {meta['cpu']}, {meta['platform']}",
        f"- **Browser:** Chromium {meta['chromium']}, {'headless' if meta['headless'] else 'visible window'}, "
        f"WebGL on `{meta['webgl_renderer']}`",
        f"- **Packages:** Python {meta['python']}, {packages}",
        "- **Plot check:** " + ("every transport drew the same traces, point counts and axis ranges"
                                 if not meta["fingerprint_mismatches"]
                                 else "MISMATCHES: " + "; ".join(meta["fingerprint_mismatches"])),
    ]


def speed_table(run, app, variants):
    header = [*shape_header(run), "orjson, none (baseline)"] + [variant_name(*v) for v in variants if v != BASELINE]
    lines = ["| " + " | ".join(header) + " |", separator(len(header))]
    for points, traces in shape_pairs(run):
        base = case(run, app, *BASELINE, points, traces)
        cells = [*shape_cells(run, points, traces), f"{ms(base and base['total_ms'])} ms"]
        for transport, compression in variants:
            if (transport, compression) == BASELINE:
                continue
            this = case(run, app, transport, compression, points, traces)
            ratio = speedup(run, app, transport, compression, points, traces)
            cells.append(f"{ms(this['total_ms'])} ms {speed_text(ratio)}" if this else "failed")
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def phase_table(run, app):
    header = [*shape_header(run), "Variant", "Wire MB", "Raw MB", "Build", "Encode", "Gzip", "Wait",
              "Download", "Browser", "Total"]
    if app == "fastapi":
        header[-1:-1] = ["(decode", "plot", "paint)"]
    lines = ["| " + " | ".join(header) + " |", separator(len(header))]
    variants = sorted({(c["transport"], c["compression"]) for c in run["cases"]},
                      key=lambda v: (COMPRESSION_ORDER.index(v[1]), TRANSPORTS.index(v[0])))
    for points, traces in shape_pairs(run):
        for transport, compression in variants:
            c = case(run, app, transport, compression, points, traces)
            shape = " | ".join(shape_cells(run, points, traces))
            if not c:
                errors = run["by_key"].get((app, transport, compression, points, traces), {}).get("errors")
                lines.append(f"| {shape} | {variant_name(transport, compression)} | "
                             f"failed: {'; '.join(errors or ['no samples'])} |")
                continue
            cells = [shape, variant_name(transport, compression),
                     mb(c["wire_bytes"]), mb(c["raw_bytes"]), ms(c.get("build_ms")), ms(c.get("encode_ms")),
                     ms(c.get("compress_ms")),
                     ms(c["wait_ms"]), ms(c["download_ms"]), ms(c["browser_ms"])]
            if app == "fastapi":
                cells += [ms(c.get("decode_ms")), ms(c.get("plot_ms")), ms(c.get("paint_ms"))]
            cells.append(f"**{ms(c['total_ms'])}**")
            lines.append("| " + " | ".join(cells) + " |")
    return lines


def gap_table(run):
    """Dash vs FastAPI for the same transport, compression and shape."""
    header = [*shape_header(run), "Variant", "FastAPI ms", "Dash ms", "Dash / FastAPI", "Wire MB FastAPI / Dash"]
    lines = ["| " + " | ".join(header) + " |", separator(len(header))]
    flagged = 0
    variants = sorted({(c["transport"], c["compression"]) for c in run["cases"]},
                      key=lambda v: (COMPRESSION_ORDER.index(v[1]), TRANSPORTS.index(v[0])))
    for points, traces in shape_pairs(run):
        for variant in variants:
            f = case(run, "fastapi", *variant, points, traces)
            d = case(run, "dash", *variant, points, traces)
            if not f or not d:
                continue
            ratio = d["total_ms"] / f["total_ms"]
            flag = f"⚠️ **{ratio:.2f}x**" if ratio >= GAP_FLAG else f"{ratio:.2f}x"
            flagged += ratio >= GAP_FLAG
            shape = " | ".join(shape_cells(run, points, traces))
            lines.append(f"| {shape} | {variant_name(*variant)} | {ms(f['total_ms'])} | "
                         f"{ms(d['total_ms'])} | {flag} | {mb(f['wire_bytes'])} / {mb(d['wire_bytes'])} |")
    return flagged, lines


def suite_section(run, name, heading, intro):
    out = [f"## {heading}", "", *intro, "", *environment(run), ""]
    timed = [c for c in compressions(run) if c != "none"] if name == "quick" else compressions(run)
    # Chart files are named after the suite, e.g. charts/forecast-dash-gzip1.svg.
    for app in ("fastapi", "dash"):
        if not any(c["app"] == app for c in run["cases"]):
            continue
        out += [f"### {APP_NAMES[app]}", ""]
        for compression in timed:
            variants = [(t, compression) for t in TRANSPORTS]
            filename = f"{name}-{app}-{compression}.svg"
            title = f"{APP_NAMES[app]}: {compression if compression != 'none' else 'no compression'}"
            text = speedup_chart(run, app, variants, title, filename)
            out += [f"![{title}](charts/{filename})", "",
                    "<sub>Same numbers as the chart (speedup vs orjson without compression): "
                    + "; ".join(text) + ".</sub>", ""]
        out += ["Total time, request sent to plot painted (median), one table per compression level:", ""]
        for compression in timed:
            out += [f"**{compression if compression != 'none' else 'No compression'}**", "",
                    *speed_table(run, app, [(t, compression) for t in TRANSPORTS]), ""]
        out += ["<details><summary>Where the time goes</summary>", "",
                "Build (Dash: running the callback, i.e. building the go.Figure), Encode and Gzip are "
                "measured on the server. Wait is request sent to first byte (includes them), Download is "
                "first to last byte, Browser is last byte to painted."
                + (" For FastAPI, Browser splits into decode (JSON.parse or Arrow read, building traces), "
                   "plotly.js, and paint." if app == "fastapi" else ""),
                "", *phase_table(run, app), "", "</details>", ""]
    if {"fastapi", "dash"} <= {c["app"] for c in run["cases"]}:
        flagged, lines = gap_table(run)
        out += ["### Dash vs FastAPI", "",
                f"Same transport, compression and data. ⚠️ marks Dash taking {GAP_FLAG}x FastAPI's time or more "
                f"({flagged} cases). In JSON, Dash's figure repeats the timestamps in every trace, where "
                "FastAPI sends each column once, so its payload grows with the trace count.", "",
                "<details><summary>All cases</summary>", "", *lines, "", "</details>", ""]
    return out


def main():
    quick, full, forecast, micro = load("quick"), load("full"), load("forecast"), load("optimisations")
    out = [
        "# Benchmark results",
        "",
        "Generated by `report.py` from `results/*.json`; the summary is in the [README](README.md).",
        "",
        "**How to read this.** Each sample is one draw in a fresh browser context: the page sends one "
        "request and the time runs until the frame showing the new plot is painted. Every case is the "
        "median of several samples. The baseline (1x) is **orjson without compression**, for the same app and "
        "the same data. Speedup = baseline time / this time: ▲ + means faster than the baseline, ▼ - means "
        "slower, and = means within 5%. The server and the browser run on one machine (loopback), so there "
        "is no real network: compression only adds CPU time here. Wire sizes are listed so you can work out "
        "transfer times for your own link.",
        "",
        "**Transports.** *orjson*: stock Dash 4.5.0 / `orjson.dumps`. *msgspec*: Dash PR #4067 / "
        "`msgspec.json`. *arrow*: Arrow IPC with stock plotly.js 4.3.0 (timestamps become ISO strings in the "
        "browser, the fast way chosen in section 4). *arrow-ms*: the same Arrow response, drawn by plotly.js "
        "with [patches/optimisations/plotly-date-typed-array.patch]"
        "(patches/optimisations/plotly-date-typed-array.patch), so timestamps go to plotly.js as a "
        "Float64Array of UTC ms. It needs that plotly.js change; the other three run on stock plotly.js.",
        "",
        "**Apps.** FastAPI + Plotly.js is written like a typical dashboard: the page gets the dataframe's "
        "columns and builds one trace per column, all sharing one x array. Dash is written like a typical "
        "Dash app: a callback builds `go.Figure()` and `add_trace(go.Scatter(...))` per column from a "
        "pandas DataFrame; all transports get that same figure.",
        "",
    ]
    if quick:
        out += suite_section(quick, "quick", "1. Quick benchmark: scale, gzip level 1", [
            "Float32 values with a UTC timestamp per minute, drawn as scattergl lines. Every transport with "
            "gzip level 1 at 10k, 1M and 10M points, plus the uncompressed orjson baseline. "
            "**Points = rows x traces**, so 10M points is 10M rows for 1 trace or 100k rows for 100 traces."])
    if full:
        out += suite_section(full, "full", "2. Full benchmark: scale, no compression, gzip 1, gzip 6", [
            "The same data as section 1, every transport at every compression level, from 5k to 10M points."])
    if forecast:
        out += suite_section(forecast, "forecast", "3. Forecast workload: per-site forecasts", [
            "Shaped like a dashboard of per-site forecasts: a week (336 rows) or 30 days (1,440 rows) of "
            "half-hourly float64 values with 2% nulls, for 50, 200 or 500 sites, drawn as stacked SVG "
            "`scatter` traces (`stackgroup`). Dash's DataFrame has a tz-aware (UTC) index, so plotly.py "
            "and every encoder take the slow path for its timestamps. A Europe/London index costs more "
            "still; dropping the zone removes most of that server time (section 4, a side finding)."])
    if micro:
        out += micro_section(micro)
    (ROOT / "benchmark.md").write_text("\n".join(out).rstrip() + "\n")
    print("Wrote benchmark.md")


def micro_section(micro):
    """Optional optimisations, measured on their own and kept out of the numbers above."""
    out = ["## 4. Optimisations (separate microbenchmarks)", "",
           "Each one changes one thing on a small sample, measured on its own. The timestamp one decided "
           "how the *arrow* and *arrow-ms* transports above handle dates.", ""]
    for item in micro["experiments"]:
        where = f", WebGL on `{item['webgl_renderer']}`" if item.get("webgl_renderer") else ""
        out += [f"### {item['title']}", "", item["description"], "",
                f"- **Command:** `{item['command']}`",
                f"- **Finished:** {item['finished']} (median of {item['repeats']}){where}", ""]
        if "columns" in item:  # one column per mode, each compared with the baseline mode
            modes, base = list(item["columns"]), item["baseline"]
            header = ["Case", *item["columns"].values()]
            out += ["| " + " | ".join(header) + " |", separator(len(header))]
            for row in item["rows"]:
                totals = {mode: row["modes"][mode]["total_ms"] for mode in modes}
                cells = [row["case"]] + [
                    f"{ms(totals[mode])} ms" + ("" if mode == base else f" {speed_text(totals[base] / totals[mode])}")
                    for mode in modes]
                out.append("| " + " | ".join(cells) + " |")
            if "detail" in item:  # where the time goes, per mode
                header = ["Case", "Variant", *item["detail"].values(), "Total", "Wire MB"]
                summary = item.get("detail_summary") or ("Where the time goes" if item["detail"] else "Sizes")
                out += ["", f"<details><summary>{summary}</summary>", "",
                        "| " + " | ".join(header) + " |", separator(len(header))]
                for row in item["rows"]:
                    for mode, label in item["columns"].items():
                        m = row["modes"][mode]
                        out.append("| " + " | ".join([row["case"], label, *(ms(m[k]) for k in item["detail"]),
                                                       f"**{ms(m['total_ms'])}**", f"{m['wire_bytes'] / 1e6:.2f}"])
                                   + " |")
                out += ["", "</details>"]
        else:  # without / with
            out += ["| Case | Without (ms) | With (ms) | Change |", separator(4)]
            for row in item["rows"]:
                out.append(f"| {row['case']} | {ms(row['without_ms'])} | {ms(row['with_ms'])} | "
                           f"{speed_text(row['without_ms'] / row['with_ms'])} |")
        out += ["", item.get("notes", ""), ""]
    return out


if __name__ == "__main__":
    main()

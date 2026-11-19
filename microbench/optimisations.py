"""Optional optimisations, each measured on its own small sample.

    .venv/bin/python microbench/optimisations.py

Nothing here is used by bench.py; results go to results/optimisations.json and
section 4 of benchmark.md. Running one experiment (--only) keeps the others' results.

1. Timestamps: toISOString, fast ISO strings, Date objects, or UTC ms with
   patches/optimisations/plotly-date-typed-array.patch. Measured in the browser
   on the same Arrow payload: decode + plot + paint.
2. Dash: send a column shared by several traces once (microbench/dash_shared_x.py).
   Measured end to end like bench.py, Arrow with gzip1.
3. Dash app code: a tz-aware Europe/London index versus the same wall-clock
   times without a zone (microbench/dash_tz_index.py). End to end, every transport.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bench  # noqa: E402
from data import make_frame  # noqa: E402
from transports import PLOTLY_BUNDLES, arrow_ipc, encode_columns  # noqa: E402

BUILD = ROOT / ".build"
WORKLOAD_NAMES = {"scale": "Scale (scattergl, float32)", "forecast": "Forecast (stacked SVG, float64, nulls)"}
PAGE = (b'<!doctype html><meta charset="utf-8"><div id="graph" style="width:1200px"></div>'
        b'<script src="/plotly.min.js"></script><script src="/date_axis.js"></script>')


CONTENT_TYPES = {".js": "text/javascript", ".json": "application/json",
                 ".arrow": "application/vnd.apache.arrow.stream"}


def server_for(files):
    """A Playwright route handler serving the page and payload from memory."""
    def serve(route):
        path = route.request.url.removeprefix("http://microbench.local")
        route.fulfill(body=files[path], content_type=CONTENT_TYPES.get(Path(path).suffix, "text/html"))
    return serve


# mode: (label, payload, plotly.js build). Conversions are in web/dates.js.
MODES = {
    "json": ("orjson JSON, stock plotly.js (baseline)", "/data.json", "stock"),
    "iso": ("Arrow, toISOString, stock plotly.js", "/data.arrow", "stock"),
    "fast-iso": ("Arrow, fast ISO strings, stock plotly.js", "/data.arrow", "stock"),
    "date": ("Arrow, Date objects, stock plotly.js", "/data.arrow", "stock"),
    "ms": ("Arrow, UTC ms, patched plotly.js", "/data.arrow", "patched"),
    "json-no-dates": ("No timestamps: orjson JSON", "/no-dates.json", "stock"),
    "arrow-no-dates": ("No timestamps: Arrow", "/no-dates.arrow", "stock"),
}
WITH_DATES = ("json", "iso", "fast-iso", "date", "ms")


def payloads(frame):
    no_dates = frame.drop("timestamp")
    return {
        "/data.json": encode_columns(frame, "orjson")[0],
        "/data.arrow": arrow_ipc(frame),
        "/no-dates.json": encode_columns(no_dates, "orjson")[0],
        "/no-dates.arrow": arrow_ipc(no_dates),
    }


def plotly_builds():
    builds = {name: (BUILD / "fastapi" / bundle).read_bytes() for name, bundle in PLOTLY_BUNDLES.items()}
    return {"stock": builds["stock"], "patched": builds["typed-dates"]}


def date_axis_experiment(browser, shapes, repeats):
    builds = plotly_builds()
    script = (BUILD / "microbench/date_axis.js").read_bytes()

    rows = []
    for workload, points, traces in shapes:
        files = {"/": PAGE, "/date_axis.js": script, **payloads(make_frame(points, traces, workload))}
        samples = {mode: [] for mode in MODES}
        for _ in range(repeats):
            for mode, (_label, url, build) in MODES.items():
                # In UTC, Date objects show the same times as the strings, so every mode draws one chart.
                context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="UTC")
                context.route(lambda url: url.startswith("http://microbench.local/"),
                              server_for({**files, "/plotly.min.js": builds[build]}))
                page = context.new_page()
                page.goto("http://microbench.local/")
                samples[mode].append(page.evaluate("window.runCase", {"mode": mode, "url": url, "workload": workload}))
                context.close()
        for group in (WITH_DATES, ("json-no-dates", "arrow-no-dates")):
            prints = {json.dumps(samples[mode][0]["fingerprint"], sort_keys=True) for mode in group}
            if len(prints) != 1:
                sys.exit(f"plots differ for {workload} {points}x{traces} in {group}: {prints}")
        rows.append({
            "case": f"{WORKLOAD_NAMES[workload]}: {points // traces:,} rows x {traces} trace{'s' if traces > 1 else ''}",
            "modes": {mode: {key: median(run[key] for run in runs) for key in ("total_ms", "decode_ms", "plot_ms")}
                      for mode, runs in samples.items()},
        })
        print(json.dumps(rows[-1]), flush=True)
    return {
        "title": "Timestamps: four ways to hand Arrow dates to plotly.js",
        "description": (
            "Browser only (no server, no network): decode the payload, build traces, `Plotly.react`, paint. "
            "Same Arrow payload for every Arrow mode; the conversions are in [web/dates.js](web/dates.js). "
            "*toISOString*: one Date and string per value. *Fast ISO strings*: the same strings with the "
            "date part made once per day and the time part once per second of the day. *Date objects*: what "
            "I use today when passing Arrow data to plotly.js by hand. *UTC ms, patched plotly.js*: a Float64Array "
            "that plotly.js takes as is after the one-condition change in "
            "[patches/optimisations/plotly-date-typed-array.patch](patches/optimisations/plotly-date-typed-array.patch). "
            "The last two columns drop the timestamp column (row index on a linear axis) to show what dates "
            "cost. The browser runs in UTC so all modes with dates draw the same axis ranges, which is checked."),
        "notes": (
            "Date objects are shown in the browser's local time, ISO strings and patched UTC ms as written; "
            "only the first suits a chart that must follow the viewer's timezone. Patched plotly.js reads "
            "numbers on a date axis differently from today (local-time ms), so upstream it would need to "
            "be opt-in.\n\n"
            "*Tried, not adopted:* making the UTC ms on the server (casting the timestamp column to float64) "
            "saved only a few percent over converting in the browser. It also moves an O(n) cast to the "
            "server, and the payload no longer carries the Arrow timestamp type."),
        "baseline": "json",
        "columns": {mode: label for mode, (label, _url, _build) in MODES.items()},
        "rows": rows,
    }


SHARED_X_NOTES = (
    "*Not adopted.* It makes the payload about 15% smaller, but the total barely moves: forecast charts "
    "have few rows per trace, so the repeated timestamps are a small part of the work next to building "
    "the go.Figure and drawing stacked SVG. FastAPI already sends x once.")


def shared_x_experiment(browser, shapes, repeats):
    results = {}
    for label, script in (("without", None), ("with", "microbench/dash_shared_x.py")):
        process, base = bench.start_server("dash", script)
        try:
            bench.measure(browser, base, "dash", "forecast", "arrow", "gzip1", 1_000, 1, 120)  # warm up
            for points, traces in shapes:
                samples = [bench.measure(browser, base, "dash", "forecast", "arrow", "gzip1", points, traces, 300)
                           for _ in range(repeats)]
                errors = [s["error"] for s in samples if "error" in s]
                if errors:
                    sys.exit(f"{label} {points}x{traces}: {errors[0]}")
                results[(label, points, traces)] = samples
        finally:
            process.terminate()
            process.wait()

    rows = []
    for points, traces in shapes:
        without, with_ = results[("without", points, traces)], results[("with", points, traces)]
        if without[0]["fingerprint"] != with_[0]["fingerprint"]:
            sys.exit(f"plots differ for {points}x{traces}")
        size = lambda n: f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} kB"  # noqa: E731
        rows.append({"case": f"Forecast: {points // traces:,} rows x {traces} traces "
                             f"(wire {size(without[0]['wire_bytes'])} -> {size(with_[0]['wire_bytes'])})",
                     "without_ms": median(s["total_ms"] for s in without),
                     "with_ms": median(s["total_ms"] for s in with_)})
        print(rows[-1], flush=True)
    return {
        "title": "Dash: send an array repeated across traces once",
        "description": (
            "Dash with Arrow and gzip1, end to end, forecast workload (go.Figure + go.Scatter, as "
            "a typical Dash app builds charts). go.Figure copies x into every trace. *Without*: the patch "
            "as benchmarked, one Arrow column per trace, each converted to dates in the browser. *With*: "
            "[microbench/dash_shared_x.py](microbench/dash_shared_x.py) reuses the column of an earlier "
            "array with identical contents, so x goes once."),
        "notes": SHARED_X_NOTES,
        "rows": rows,
    }


TZ_TRANSPORTS = ("orjson", "msgspec", "arrow", "arrow-ms")
TZ_INDEXES = {"tz": "tz-aware index", "naive": "zone dropped"}


def tz_index_experiment(browser, shapes, repeats):
    results = {}
    for index in TZ_INDEXES:
        process, base = bench.start_server("dash", "microbench/dash_tz_index.py", ["--index", index])
        try:
            bench.measure(browser, base, "dash", "forecast", "orjson", "none", 1_000, 1, 120)  # warm up
            for points, traces in shapes:
                for _ in range(repeats):
                    for transport in TZ_TRANSPORTS:
                        sample = bench.measure(browser, base, "dash", "forecast", transport, "none",
                                               points, traces, 300)
                        if "error" in sample:
                            sys.exit(f"{index} {transport} {points}x{traces}: {sample['error']}")
                        results.setdefault((index, transport, points, traces), []).append(sample)
                        print(f"{index:5} {transport:8} {points:>9,} x {traces:<3} {sample['total_ms']:8.0f} ms",
                              flush=True)
        finally:
            process.terminate()
            process.wait()

    columns = {f"{index}-{transport}": f"{label}, {transport}"
               for index, label in TZ_INDEXES.items() for transport in TZ_TRANSPORTS}
    rows = []
    for points, traces in shapes:
        groups = {f"{index}-{transport}": results[(index, transport, points, traces)]
                  for index in TZ_INDEXES for transport in TZ_TRANSPORTS}
        prints = {json.dumps(samples[0]["fingerprint"], sort_keys=True) for samples in groups.values()}
        if len(prints) != 1:
            sys.exit(f"plots differ for {points}x{traces}: {prints}")
        rows.append({
            "case": f"{points // traces:,} rows x {traces} traces",
            "modes": {key: {k: median(s[k] for s in samples)
                            for k in ("total_ms", "build_ms", "encode_ms", "wait_ms", "browser_ms", "wire_bytes")}
                      for key, samples in groups.items()},
        })
    return {
        "id": "tz-index",
        "title": "Dash app code: drop the timezone from the index before plotting",
        "description": (
            "Dash end to end, like bench.py: request sent to plot painted, no compression, forecast workload "
            "(half-hourly float64 with nulls, stacked SVG `scatter`). The DataFrame index is in "
            "Europe/London (from `tz_convert`), either **tz-aware** or with the zone dropped "
            "(`df.index.tz_convert(\"Europe/London\").tz_localize(None)`), served by "
            "[microbench/dash_tz_index.py](microbench/dash_tz_index.py). plotly.py stores a tz-aware index "
            "as an object array of `pd.Timestamp` in every trace, and every encoder then walks it in Python. "
            "Both draw the same chart (plotly.js shows a date string's wall-clock time and ignores its "
            "offset), which is checked. Baseline: tz-aware index with stock Dash (orjson). *arrow-ms* uses the "
            "patched plotly.js."),
        "notes": (
            "*Side finding, optional and out of scope for the transport work:* an app change, no Dash or "
            "plotly.js change. Dropping the zone draws the same chart and helps every transport. The main "
            "benchmarks keep the tz-aware index. The end-to-end forecast benchmark (section 3) uses a UTC "
            "index, which plotly.py converts faster than a London one, so its Dash *Build* and JSON "
            "*Encode* times understate this cost."),
        "baseline": "tz-orjson",
        "columns": columns,
        "detail": {"build_ms": "Build", "encode_ms": "Encode", "wait_ms": "Wait", "browser_ms": "Browser"},
        "rows": rows,
    }


EXPERIMENTS = ("date-axis", "shared-x", "tz-index")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--only", choices=EXPERIMENTS, help="run one experiment")
    parser.add_argument("--small", action="store_true", help="smallest shapes only, for a quick look")
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "optimisations.json")
    args = parser.parse_args()
    date_shapes = [("forecast", 1_440 * 50, 50), ("forecast", 1_440 * 500, 500),
                   ("scale", 1_000_000, 1), ("scale", 1_000_000, 10)]
    shared_shapes = [(336 * 50, 50), (1_440 * 50, 50), (1_440 * 200, 200), (1_440 * 500, 500)]
    tz_shapes = shared_shapes
    if args.small:
        date_shapes, shared_shapes = [("forecast", 1_440 * 50, 50), ("scale", 1_000_000, 1)], [(1_440 * 500, 500)]
        tz_shapes = [(1_440 * 50, 50)]
    selected = [args.only] if args.only else list(EXPERIMENTS)

    experiments = {}
    with sync_playwright() as playwright:
        browser, renderer = bench.launch(playwright, args.headless)
        runners = {"date-axis": lambda: date_axis_experiment(browser, date_shapes, args.repeats),
                   "shared-x": lambda: shared_x_experiment(browser, shared_shapes, args.repeats),
                   "tz-index": lambda: tz_index_experiment(browser, tz_shapes, args.repeats)}
        for key in selected:
            experiments[key] = {"id": key, "webgl_renderer": renderer, **runners[key]()}
        browser.close()

    run = {"command": "python " + " ".join(["microbench/optimisations.py", *sys.argv[1:]]),
           "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "repeats": args.repeats}
    out = args.out
    kept = json.loads(out.read_text())["experiments"] if out.exists() else []
    merged = {item["id"]: item for item in kept}
    merged.update({key: {**item, **run} for key, item in experiments.items()})
    out.parent.mkdir(parents=True, exist_ok=True)
    ordered = [merged[key] for key in EXPERIMENTS if key in merged]
    out.write_text(json.dumps({"experiments": ordered}, indent=1) + "\n")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

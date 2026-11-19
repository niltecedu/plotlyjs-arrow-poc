"""Headless benchmark: drive the FastAPI and Dash pages with Playwright.

    .venv/bin/python bench.py quick      # scale: gzip1 only, 10k / 1M / 10M points
    .venv/bin/python bench.py full       # scale: none / gzip1 / gzip6, 5k ... 10M points
    .venv/bin/python bench.py forecast   # per-site forecasts: 336 / 1,440 rows x 50 / 200 / 500 sites

Every sample opens a fresh browser context (empty cache, new JS heap), loads
the app with the case preset in the URL, clicks Draw and reads the result the
page records: request sent -> plot painted, plus phases. Cases are shuffled
within each repeat so slow drift does not favour one transport.
"""

import argparse
import json
import os
import platform
import random
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from statistics import median

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

# Headless Chromium only has software WebGL (SwiftShader), which made painting
# 1M points take ~18 s and buried every transport difference. By default the
# benchmark runs a visible Chromium on the GPU. Under WSL2 that needs Mesa's
# D3D12 driver, pointed at the discrete GPU.
GPU_ENV = {"GALLIUM_DRIVER": "d3d12", "MESA_D3D12_DEFAULT_ADAPTER_NAME": "NVIDIA"}
GPU_ARGS = ["--ignore-gpu-blocklist", "--use-angle=gl"]
SOFTWARE_RENDERERS = ("SwiftShader", "llvmpipe", "softpipe")
WEBGL_RENDERER = """() => {
    const gl = document.createElement('canvas').getContext('webgl');
    const info = gl && gl.getExtension('WEBGL_debug_renderer_info');
    return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : 'no WebGL';
}"""

ROOT = Path(__file__).resolve().parent
APPS = {"fastapi": "fastapi_app.py", "dash": "dash_app.py"}
METRICS = ("total_ms", "wait_ms", "download_ms", "browser_ms", "build_ms", "encode_ms",
           "compress_ms", "decode_ms", "plot_ms", "paint_ms", "wire_bytes", "raw_bytes")

TRANSPORTS = ["orjson", "msgspec", "arrow", "arrow-ms"]
SUITES = {
    # (orjson, none) is always included: it is the 1x baseline.
    "quick": {"workload": "scale", "points": [10_000, 1_000_000, 10_000_000], "traces": [1, 10, 100],
              "compressions": ["gzip1"]},
    "full": {"workload": "scale", "points": [5_000, 50_000, 500_000, 5_000_000, 10_000_000],
             "traces": [1, 10, 100], "compressions": ["none", "gzip1", "gzip6"]},
    # A week or 30 days of half-hourly forecasts for 50, 200 or 500 sites.
    "forecast": {"workload": "forecast", "rows": [336, 1_440], "traces": [50, 200, 500],
                 "compressions": ["none", "gzip1", "gzip6"]},
}


def shapes_for(suite, points=None, traces=None):
    """(points, traces) pairs: a points sweep, or rows x traces for forecasts."""
    traces = traces or suite["traces"]
    if points or "points" in suite:
        return [(p, t) for p in (points or suite["points"]) for t in traces]
    return [(rows * t, t) for rows in suite["rows"] for t in traces]


def variants_for(suite):
    return sorted({("orjson", "none")} | {(t, c) for t in TRANSPORTS for c in suite["compressions"]})


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(app, script=None, args=()):
    port = free_port()
    process = subprocess.Popen([sys.executable, script or APPS[app], "--port", str(port), *args], cwd=ROOT,
                               stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base, timeout=1)
            return process, base
        except OSError:
            time.sleep(0.2)
    process.kill()
    raise RuntimeError(f"{app} did not start")


def launch(playwright, headless):
    if headless:
        browser = playwright.chromium.launch()
    else:
        browser = playwright.chromium.launch(headless=False, args=GPU_ARGS, env={**os.environ, **GPU_ENV})
    page = browser.new_page()
    renderer = page.evaluate(WEBGL_RENDERER)
    page.close()
    if not headless and any(name in renderer for name in SOFTWARE_RENDERERS):
        browser.close()
        sys.exit(f"WebGL is using a software renderer ({renderer}); use --headless or fix the GPU setup")
    return browser, renderer


def measure(browser, base, app, workload, transport, compression, points, traces, timeout_s):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="UTC")
    page = context.new_page()
    try:
        query = (f"workload={workload}&transport={transport}&compression={compression}"
                 f"&points={points}&traces={traces}")
        page.goto(f"{base}/?{query}")
        ready = "!document.getElementById('draw').disabled" if app == "fastapi" else "window.dashGraphReady"
        page.wait_for_function(ready)
        page.click("#draw")
        page.wait_for_function("window.benchResults?.length || window.benchError",
                               timeout=timeout_s * 1000, polling=100)
        error = page.evaluate("window.benchError")
        if error:
            return {"error": error}
        result = page.evaluate("window.benchResults[0]")
        expected = "arrow" if transport.startswith("arrow") else "json"
        if result["sent"] != expected:
            return {"error": f"server sent {result['sent']}, expected {expected}"}
        if result["typed_dates"] != (transport == "arrow-ms"):
            return {"error": f"wrong plotly.js build for {transport}"}
        return result
    except PlaywrightError as error:
        return {"error": str(error).splitlines()[0]}
    finally:
        context.close()


def check_fingerprints(samples):
    """Every transport must draw the same thing for the same app and shape."""
    problems = []
    by_shape = {}
    for s in samples:
        if "fingerprint" in s:
            by_shape.setdefault((s["app"], s["points"], s["traces"]), []).append(s)
    for (app, points, traces), group in by_shape.items():
        ref = group[0]["fingerprint"]
        if ref["traces"] != traces or ref["points"] != points:
            problems.append(f"{app} {points}x{traces}: drew {ref['traces']} traces / {ref['points']} points")
        for s in group[1:]:
            fp = s["fingerprint"]
            same_y = all(abs(a - b) <= 1e-4 * max(1, abs(a)) for a, b in zip(fp["yrange"], ref["yrange"]))
            if (fp["traces"], fp["points"], fp["xrange"]) != (ref["traces"], ref["points"], ref["xrange"]) or not same_y:
                problems.append(f"{app} {points}x{traces} {s['transport']}: {fp} != {ref}")
    return problems


def summarise(samples):
    groups = {}
    for s in samples:
        key = (s["app"], s["transport"], s["compression"], s["points"], s["traces"])
        groups.setdefault(key, []).append(s)
    cases = []
    for (app, transport, compression, points, traces), group in sorted(groups.items()):
        ok = [s for s in group if "error" not in s]
        case = {"app": app, "transport": transport, "compression": compression,
                "points": points, "traces": traces, "samples": len(ok),
                "errors": sorted({s["error"] for s in group if "error" in s})}
        for metric in METRICS:
            values = [s[metric] for s in ok if metric in s]
            if values:
                case[metric] = round(median(values), 3)
        cases.append(case)
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suite", choices=SUITES)
    parser.add_argument("--apps", nargs="+", choices=APPS, default=list(APPS))
    parser.add_argument("--points", nargs="+", type=int, help="override the suite's point counts")
    parser.add_argument("--traces", nargs="+", type=int, help="override the suite's trace counts")
    parser.add_argument("--repeats", type=int, default=3, help="samples per case (median is reported)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds per sample")
    parser.add_argument("--headless", action="store_true",
                        help="headless Chromium (software WebGL: paint times are not realistic)")
    parser.add_argument("--out", type=Path, help="default: results/<suite>.json")
    args = parser.parse_args()

    suite = SUITES[args.suite]
    workload = suite["workload"]
    shapes = shapes_for(suite, args.points, args.traces)
    variants = variants_for(suite)
    out = args.out or ROOT / "results" / f"{args.suite}.json"
    rng = random.Random(0)
    samples = []
    started = time.perf_counter()

    with sync_playwright() as playwright:
        browser, renderer = launch(playwright, args.headless)
        print(f"WebGL renderer: {renderer}", flush=True)
        for app in args.apps:
            process, base = start_server(app)
            try:
                # Warm the server's imports and code paths once; not recorded.
                for transport, compression in variants:
                    measure(browser, base, app, workload, transport, compression, 1_000, 1, args.timeout)
                for points, traces in shapes:
                    for repeat in range(args.repeats):
                        for transport, compression in rng.sample(variants, len(variants)):
                            result = measure(browser, base, app, workload, transport, compression,
                                             points, traces, args.timeout)
                            samples.append({**result, "app": app, "transport": transport,
                                            "compression": compression, "points": points,
                                            "traces": traces, "repeat": repeat})
                            status = result.get("error") or f"{result['total_ms']:.0f} ms"
                            print(f"{app:7} {points:>10,} x {traces:<3} {transport:7} "
                                  f"{compression:5} #{repeat}  {status}", flush=True)
            finally:
                process.terminate()
                process.wait()
            write_results(out, args, suite, renderer, browser.version, started, samples)
        browser.close()


def write_results(out, args, suite, renderer, chromium, started, samples):
    """Written after each app, so an interrupted run keeps what it measured."""
    problems = check_fingerprints(samples)
    for problem in problems:
        print("MISMATCH", problem, file=sys.stderr)
    meta = {
        "suite": args.suite,
        "workload": suite["workload"],
        "command": "python " + " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]),
        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "minutes": round((time.perf_counter() - started) / 60, 1),
        "repeats": args.repeats,
        "platform": platform.platform(),
        "cpu": _cpu_name(),
        "python": platform.python_version(),
        "chromium": chromium,
        "headless": args.headless,
        "webgl_renderer": renderer,
        "packages": {name: version(name) for name in
                     ("dash", "plotly", "fastapi", "uvicorn", "orjson", "msgspec", "polars", "pyarrow", "pandas", "numpy")},
        "npm": {name: json.loads((ROOT / "node_modules" / name / "package.json").read_text())["version"]
                for name in ("plotly.js-dist-min", "apache-arrow")},
        "fingerprint_mismatches": problems,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, "cases": summarise(samples), "samples": samples}, indent=1) + "\n")
    print(f"Wrote {out} ({meta['minutes']} min)")


def _cpu_name():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


if __name__ == "__main__":
    main()

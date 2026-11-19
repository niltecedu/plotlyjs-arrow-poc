"""FastAPI + Plotly.js: interactive page and benchmark target.

    .venv/bin/python fastapi_app.py            # http://127.0.0.1:8000

Written the way a typical FastAPI + Plotly.js dashboard draws a dataframe: the
page asks for the dataframe's columns in the chosen transport and compression,
builds one trace per column (all sharing one x array) and plots it.
"""

import argparse
from pathlib import Path
from time import perf_counter
from typing import Literal

import uvicorn
from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from data import DATASETS, PRESET_POINTS, PRESET_TRACES, CurrentFrame, make_frame, rows_for
from transports import COMPRESSIONS, TRANSPORTS, compress, encode_columns, plotly_bundle, server_timing

ROOT = Path(__file__).resolve().parent
NO_STORE = {"Cache-Control": "no-store"}

app = FastAPI(title="FastAPI + Plotly.js transport benchmark")
app.mount("/static", StaticFiles(directory=ROOT / ".build" / "fastapi"), name="static")
current = CurrentFrame()

# Run each encoder once so the first timed request does not pay for lazy imports.
for _workload in DATASETS:
    for _transport in TRANSPORTS:
        encode_columns(make_frame(100, 1, _workload), _transport)

Workload = Literal["scale", "forecast"]
Transport = Literal["orjson", "msgspec", "arrow", "arrow-ms"]
Compression = Literal["none", "gzip1", "gzip6"]


@app.post("/prepare")
def prepare(points: int, traces: int, workload: Workload):
    """Load the dataframe before the timed request, so generating it is not timed."""
    current.load(points, traces, workload)
    return Response(status_code=204, headers=NO_STORE)


@app.get("/columns")
def columns(workload: Workload, transport: Transport, compression: Compression, points: int, traces: int,
            nonce: str = Query(description="Unique per request, so no cache can answer it")):
    frame = current.load(points, traces, workload)
    start = perf_counter()
    body, mimetype = encode_columns(frame, transport)
    encode_ms = (perf_counter() - start) * 1000
    body, headers, compress_ms = compress(body, compression)
    timing = server_timing(encode=encode_ms, compress=compress_ms)
    return Response(body, media_type=mimetype,
                    headers={**headers, **NO_STORE, "Server-Timing": timing})


@app.get("/", response_class=HTMLResponse)
def page(workload: Workload = "scale", transport: Transport = "arrow", compression: Compression = "gzip1",
         points: int = 1_000_000, traces: int = 10):
    rows_for(points, traces)  # rejects shapes that do not split evenly
    return PAGE.format(
        workload=_options(DATASETS, workload),
        transport=_options(TRANSPORTS, transport),
        compression=_options(COMPRESSIONS, compression),
        points=_options(sorted({*PRESET_POINTS, points}), points, "{:,}"),
        traces=_options(sorted({*PRESET_TRACES, traces}), traces),
        plotly=plotly_bundle(transport),
    )


def _options(values, selected, label="{}"):
    return "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{label.format(value)}</option>'
        for value in values
    )


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>FastAPI + Plotly.js</title>
<link rel="stylesheet" href="/static/style.css">
<main>
  <h1>FastAPI + Plotly.js</h1>
  <p class="muted">Each draw clears the graph, then times one request from
    the moment it is sent until the plot is painted.</p>
  <div class="controls">
    <label>Workload <select id="workload">{workload}</select></label>
    <label>Transport <select id="transport">{transport}</select></label>
    <label>Compression <select id="compression">{compression}</select></label>
    <label>Points (rows x traces) <select id="points">{points}</select></label>
    <label>Traces <select id="traces">{traces}</select></label>
    <button id="draw" disabled>Draw</button>
    <button id="clear" class="secondary">Clear graph</button>
    <span id="status" class="muted"></span>
  </div>
  <div id="graph" class="graph"></div>
  <div id="results"></div>
</main>
<script src="/static/{plotly}"></script>
<script src="/static/page.js"></script>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    print(f"FastAPI + Plotly.js: http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")

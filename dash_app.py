"""Dash: interactive app and benchmark target.

    .venv/bin/python dash_app.py               # http://127.0.0.1:8050

The figure callback is written the way a typical Dash app builds its charts:
a pandas DataFrame with a DatetimeIndex, then go.Figure() + add_trace(go.Scatter(
x=df.index, y=df[col].values, ...)) per column. The "forecast" workload uses a
tz-aware index, as data converted with tz_convert() has; the "scale" workload
uses a naive one.

Every transport gets the same figure. Only the serialisation differs:

    orjson   stock Dash 4.5.0: plotly's to_json_plotly with orjson
    msgspec  dash/_json.py from Dash PR #4067 with its msgspec engine
    arrow    patches/dash-arrow.patch: the figure's arrays go as Arrow IPC
    arrow-ms the same, drawn by plotly.js with patches/optimisations/plotly-date-typed-array.patch

Dash serialises a callback response with dash._callback.to_json (and, with the
patch, tries dash._callback.to_arrow_response first). This app wraps both so
one server can switch encoder per request and report the encode time.
"""

import argparse
import json
import logging
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import dash._callback as dash_callback
import plotly.graph_objects as go
import pyarrow  # noqa: F401  the patch imports it lazily; load it before the first timed request
from dash import Dash, Input, Output, State, dcc, html
from dash._json import to_json as pr4067_to_json  # added by setup.sh from PR #4067
from flask import g, has_request_context, request
from plotly.io.json import config as plotly_json_config

from data import DATASETS, PRESET_POINTS, PRESET_TRACES, CurrentFrame
from transports import COMPRESSIONS, TRANSPORTS, compress, plotly_bundle, server_timing

ROOT = Path(__file__).resolve().parent
STYLE = json.loads((ROOT / "plot_style.json").read_text())
EMPTY_FIGURE = {"data": [], "layout": STYLE["layout"]}
TRACE_CLASSES = {"scatter": go.Scatter, "scattergl": go.Scattergl}


def to_pandas(frame, workload):
    """The app's data, as a typical Dash app holds it: pandas with a DatetimeIndex."""
    df = frame.to_pandas().set_index("timestamp")
    if workload == "scale":
        df.index = df.index.tz_localize(None)
    return df


current = CurrentFrame(convert=to_pandas)


def figure(points: int, traces: int, workload: str) -> go.Figure:
    df = current.load(points, traces, workload)
    style = dict(STYLE["traces"][workload])
    trace = TRACE_CLASSES[style.pop("type")]
    fig = go.Figure(layout=STYLE["layout"])
    for name in df.columns:
        fig.add_trace(trace(x=df.index, y=df[name].values, name=name, **style))
    return fig


# -- Per-request encoder routing ---------------------------------------------

stock_to_json = dash_callback.to_json
stock_to_arrow_response = dash_callback.to_arrow_response


def routed_to_json(response):
    start = perf_counter()
    if g.get("transport") == "msgspec":
        # PR #4067 uses msgspec when plotly's JSON engine is "json". The server
        # runs single-threaded (see __main__) so this switch affects only this response.
        plotly_json_config.default_engine = "json"
        try:
            body = pr4067_to_json(response)
        finally:
            plotly_json_config.default_engine = "auto"
    else:
        body = stock_to_json(response)
    g.encode_ms, g.sent = (perf_counter() - start) * 1000, "json"
    return body


def routed_to_arrow_response(*args):
    if not g.get("transport", "").startswith("arrow"):
        return None  # orjson / msgspec: leave the response to to_json
    start = perf_counter()
    body = stock_to_arrow_response(*args)
    if body is not None:
        g.encode_ms, g.sent = (perf_counter() - start) * 1000, "arrow"
    return body


dash_callback.to_json = routed_to_json
dash_callback.to_arrow_response = routed_to_arrow_response


def warm_up():
    """Run each encoder once so the first timed request does not pay for lazy imports."""
    def ignore_header(*_args):
        pass

    headers = SimpleNamespace(append_header=ignore_header, set_header=ignore_header)
    for workload in DATASETS:
        response = {"multi": True, "response": {"graph": {"figure": figure(100, 1, workload)}}}
        stock_to_arrow_response(response, SimpleNamespace(dash_response=headers),
                                {"Accept": "application/vnd.apache.arrow.stream"})
        stock_to_json(response)
        pr4067_to_json(response)


# -- App ------------------------------------------------------------------------

class BenchDash(Dash):
    """Loads stock or patched plotly.js, whichever the transport in the URL needs."""

    def interpolate_index(self, **kwargs):
        bundle = plotly_bundle(request.args.get("transport", "arrow"))
        kwargs["scripts"] = f'<script src="/assets/{bundle}"></script>' + kwargs["scripts"]
        return super().interpolate_index(**kwargs)


# assets_ignore: the plotly.js bundles are served, but added by BenchDash only.
app = BenchDash(__name__, assets_folder=str(ROOT / ".build" / "dash_assets"),
                assets_ignore="plotly", title="Dash transport benchmark")


def radio(id_, values, value, label="{}"):
    options = [{"label": label.format(v), "value": v} for v in values]
    return dcc.RadioItems(id=id_, options=options, value=value, inline=True)


def layout():
    # Presets come from the page URL. Dash fetches the layout from /_dash-layout,
    # so read them from the page that asked for it.
    page_url = request.referrer if has_request_context() else None
    args = dict(parse_qsl(urlsplit(page_url).query)) if page_url else {}
    points = int(args.get("points", 1_000_000))
    traces = int(args.get("traces", 10))
    return html.Main([
        html.H1("Dash"),
        html.P("Each draw clears the graph, then times the figure callback from the moment "
               "its request is sent until the plot is painted.", className="muted"),
        html.Div([
            html.Label(["Workload", radio("workload", DATASETS, args.get("workload", "scale"))]),
            html.Label(["Transport", radio("transport", TRANSPORTS, args.get("transport", "arrow"))]),
            html.Label(["Compression", radio("compression", COMPRESSIONS, args.get("compression", "gzip1"))]),
            html.Label(["Points (rows x traces)",
                        radio("points", sorted({*PRESET_POINTS, points}), points, "{:,}")]),
            html.Label(["Traces", radio("traces", sorted({*PRESET_TRACES, traces}), traces)]),
            html.Button("Draw", id="draw"),
            html.Button("Clear graph", id="clear", className="secondary"),
        ], className="controls"),
        dcc.Graph(id="graph", figure=EMPTY_FIGURE, config=STYLE["config"], className="graph"),
        dcc.Store(id="request"),
        html.Div(id="results"),
    ])


app.layout = layout


@app.callback(
    Output("graph", "figure", allow_duplicate=True),
    Output("request", "data"),
    Input("draw", "n_clicks"),
    State("workload", "value"), State("transport", "value"), State("compression", "value"),
    State("points", "value"), State("traces", "value"),
    prevent_initial_call=True,
)
def prepare(n_clicks, workload, transport, compression, points, traces):
    """Not timed: clear the graph and load the dataframe, then ask for the figure."""
    current.load(points, traces, workload)
    return EMPTY_FIGURE, {"workload": workload, "transport": transport, "compression": compression,
                          "points": points, "traces": traces, "draw": n_clicks}


@app.callback(Output("graph", "figure"), Input("request", "data"), prevent_initial_call=True)
def draw(run):
    g.transport, g.compression = run["transport"], run["compression"]
    g.case = " ".join(str(run[key]) for key in ("workload", "transport", "compression", "points", "traces"))
    start = perf_counter()
    result = figure(run["points"], run["traces"], run["workload"])
    g.build_ms = (perf_counter() - start) * 1000
    return result


app.clientside_callback(
    f"() => ({json.dumps(EMPTY_FIGURE)})",
    Output("graph", "figure", allow_duplicate=True),
    Input("clear", "n_clicks"),
    prevent_initial_call=True,
)


@app.server.after_request
def compress_and_time(response):
    """Compress the timed figure response and report server timings."""
    response.headers["Cache-Control"] = "no-store"
    if "transport" not in g:
        return response
    body, headers, compress_ms = compress(response.get_data(), g.compression)
    response.set_data(body)
    response.headers.update(headers)
    response.headers["Server-Timing"] = server_timing(
        build=g.build_ms, encode=g.encode_ms, compress=compress_ms
    ) + f', case;desc="{g.case} {g.sent}"'
    return response


def serve(port):
    warm_up()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no per-request log lines
    print(f"Dash: http://127.0.0.1:{port}", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, threaded=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8050)
    serve(parser.parse_args().port)

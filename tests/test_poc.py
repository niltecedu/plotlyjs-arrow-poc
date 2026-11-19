"""Checks for the patched Dash encoder, the FastAPI encoders and both pages.

    .venv/bin/python -m pytest tests            # browser tests run headless
"""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import orjson
import pandas as pd
import polars as pl
import plotly.graph_objects as go
import pyarrow as pa
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dash._arrow import ARROW_MIMETYPE, PLACEHOLDER, to_arrow_response  # noqa: E402

from data import make_frame  # noqa: E402
from transports import TRANSPORTS, encode_columns  # noqa: E402

ACCEPT_ARROW = {"Accept": f"application/json, {ARROW_MIMETYPE}"}


def callback_ctx():
    headers = {}
    response = SimpleNamespace(set_header=headers.__setitem__,
                               append_header=headers.__setitem__)
    return SimpleNamespace(dash_response=response), headers


def decode(body):
    """What the renderer does, in Python: envelope JSON with columns swapped in."""
    table = pa.ipc.open_stream(body).read_all()
    columns = [table.column(i)[0].values for i in range(table.num_columns)]

    def swap(value):
        if isinstance(value, dict):
            if set(value) == {PLACEHOLDER}:
                return columns[value[PLACEHOLDER]]
            return {key: swap(item) for key, item in value.items()}
        if isinstance(value, list):
            return [swap(item) for item in value]
        return value

    return swap(json.loads(table.schema.metadata[b"dash"]))


# -- Dash Arrow patch --------------------------------------------------------------


@pytest.mark.parametrize("source", ["polars", "pandas-pyarrow", "pyarrow"])
def test_arrow_response_round_trips_each_dataframe_library(source):
    frame = make_frame(20, 2)
    if source == "pandas-pyarrow":
        frame = frame.to_pandas(use_pyarrow_extension_array=True)
    elif source == "pyarrow":
        frame = frame.to_arrow()
    x = frame["timestamp"]
    response = {"multi": True, "response": {"graph": {"figure": {
        "data": [{"type": "scattergl", "x": x, "y": frame[name]} for name in ("trace_000", "trace_001")],
        "layout": {"title": {"text": "kept as JSON"}},
    }}}}
    ctx, headers = callback_ctx()

    body = to_arrow_response(response, ctx, ACCEPT_ARROW)

    assert headers["Content-Type"] == ARROW_MIMETYPE
    figure = decode(body)["response"]["graph"]["figure"]
    assert figure["layout"] == {"title": {"text": "kept as JSON"}}
    expected = make_frame(20, 2)
    for trace, name in zip(figure["data"], ("trace_000", "trace_001")):
        assert trace["type"] == "scattergl"
        # tz-aware times are sent as wall-clock time in their zone, as plotly.py's JSON does.
        assert trace["x"].type == pa.timestamp("ms")
        assert trace["x"].to_pylist() == [t.replace(tzinfo=None) for t in expected["timestamp"].to_list()]
        assert trace["y"].type == pa.float32()
        assert trace["y"].to_pylist() == expected[name].to_list()


def test_arrays_of_different_lengths_share_one_stream():
    response = {"response": {"graph": {"figure": {"data": [
        {"y": pl.Series([1.0, 2.0, 3.0])}, {"y": pl.Series([4.0], dtype=pl.Float32)}]}}}}
    figure = decode(to_arrow_response(response, callback_ctx()[0], ACCEPT_ARROW))["response"]["graph"]["figure"]
    assert [trace["y"].to_pylist() for trace in figure["data"]] == [[1.0, 2.0, 3.0], [4.0]]


def test_nulls_survive():
    response = {"response": {"g": {"figure": {"data": [{"y": pl.Series([1.0, None, 3.0])}]}}}}
    figure = decode(to_arrow_response(response, callback_ctx()[0], ACCEPT_ARROW))["response"]["g"]["figure"]
    assert figure["data"][0]["y"].to_pylist() == [1.0, None, 3.0]


def test_go_figure_arrays_go_as_arrow_with_plotly_py_dates():
    """go.Figure stores numpy; tz-aware times keep plotly.py's wall-clock meaning."""
    index = pd.date_range("2026-03-28 23:00", periods=3, freq="12h", tz="Europe/London")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=index, y=np.array([1.0, np.nan, 3.0]), text=["a", "b", "c"], stackgroup="s"))
    response = {"response": {"g": {"figure": fig}}}

    trace = decode(to_arrow_response(response, callback_ctx()[0], ACCEPT_ARROW))["response"]["g"]["figure"]["data"][0]

    plotly_json = json.loads(fig.to_json())["data"][0]["x"]  # "...T11:00:00+01:00" etc.
    assert [t.isoformat() for t in trace["x"].to_pylist()] == [s[:19] for s in plotly_json]
    assert trace["y"].to_pylist()[0] == 1.0 and np.isnan(trace["y"].to_pylist()[1])
    assert trace["text"] == ["a", "b", "c"] and trace["stackgroup"] == "s"


def test_only_figures_are_converted():
    response = {"response": {"store": {"data": pl.Series([1.0, 2.0])}, "g": {"figure": {"data": []}}}}
    assert to_arrow_response(response, callback_ctx()[0], ACCEPT_ARROW) is None


@pytest.mark.parametrize("response, headers", [
    ({"response": {"g": {"figure": {"data": [{"y": pl.Series([1.0])}]}}}}, {"Accept": "application/json"}),
    ({"response": {"g": {"figure": {"data": [{"y": [1.0, 2.0]}]}}}}, ACCEPT_ARROW),
    # int64 would reach plotly.js as a BigInt64Array, which it cannot plot.
    ({"response": {"g": {"figure": {"data": [{"y": pl.Series([1, 2], dtype=pl.Int64)}]}}}}, ACCEPT_ARROW),
], ids=["client-wants-json", "lists", "int64"])
def test_json_is_kept_when_arrow_does_not_apply(response, headers):
    ctx, sent_headers = callback_ctx()
    assert to_arrow_response(response, ctx, headers) is None
    assert "Content-Type" not in sent_headers


# -- FastAPI encoders --------------------------------------------------------------


@pytest.mark.parametrize("transport", TRANSPORTS)
def test_fastapi_encoders_send_the_whole_dataframe(transport):
    frame = make_frame(30, 3)
    body, mimetype = encode_columns(frame, transport)
    if transport.startswith("arrow"):
        assert mimetype == "application/vnd.apache.arrow.stream"
        assert pl.read_ipc_stream(io.BytesIO(body)).equals(frame)
        return
    columns = orjson.loads(body)
    assert list(columns) == frame.columns
    assert pd.to_datetime(columns["timestamp"]).tolist() == frame["timestamp"].dt.replace_time_zone(None).to_list()
    for name in frame.columns[1:]:
        np.testing.assert_allclose(columns[name], frame[name].to_numpy(), rtol=1e-7)


# -- Both pages, end to end (headless) ---------------------------------------------


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.mark.parametrize("app", ["fastapi", "dash"])
def test_every_transport_draws_the_same_plot(browser, app):
    import bench

    process, base = bench.start_server(app)
    try:
        results = {
            (workload, transport): bench.measure(browser, base, app, workload, transport, "gzip1", 3_000, 3,
                                                 timeout_s=60)
            for workload in ("scale", "forecast") for transport in TRANSPORTS
        }
    finally:
        process.terminate()
        process.wait()

    for (workload, transport), result in results.items():
        assert "error" not in result, (workload, transport, result)
        assert result["sent"] == ("arrow" if transport.startswith("arrow") else "json")
        assert result["total_ms"] >= result["browser_ms"] > 0
        assert result["wire_bytes"] < result["raw_bytes"]  # gzip was applied
    for workload in ("scale", "forecast"):
        fingerprints = [result["fingerprint"] for (w, _), result in results.items() if w == workload]
        assert fingerprints[0]["traces"] == 3 and fingerprints[0]["points"] == 3_000
        assert all(fp == fingerprints[0] for fp in fingerprints), fingerprints

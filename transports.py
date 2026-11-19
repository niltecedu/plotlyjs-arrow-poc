"""Encoders and HTTP compression shared by the FastAPI and Dash apps."""

import gzip
from time import perf_counter

import msgspec
import orjson
import polars as pl

# arrow-ms is Arrow on the wire, drawn by plotly.js with
# patches/optimisations/plotly-date-typed-array.patch (timestamps as UTC ms).
TRANSPORTS = ("orjson", "msgspec", "arrow", "arrow-ms")
COMPRESSIONS = ("none", "gzip1", "gzip6")
BASELINE = ("orjson", "none")

PLOTLY_BUNDLES = {"stock": "plotly.min.js", "typed-dates": "plotly-typed-dates.min.js"}
JSON_MIMETYPE = "application/json"
ARROW_MIMETYPE = "application/vnd.apache.arrow.stream"


# FastAPI sends the dataframe's columns. JSON gets {"timestamp": [...],
# "trace_000": [...], ...}; Arrow gets the dataframe as an IPC stream.

def encode_columns(frame: pl.DataFrame, transport: str) -> tuple[bytes, str]:
    if transport.startswith("arrow"):
        return arrow_ipc(frame), ARROW_MIMETYPE
    columns = {name: frame[name].to_numpy() for name in frame.columns}
    if transport == "orjson":
        return orjson.dumps(columns, option=orjson.OPT_SERIALIZE_NUMPY), JSON_MIMETYPE
    if transport == "msgspec":
        return _msgspec_encoder.encode(columns), JSON_MIMETYPE
    raise ValueError(f"unknown transport {transport!r}")


# msgspec has no numpy support; its documented route is an enc_hook. Dash PR
# #4067 does the same thing (numpy -> tolist()) for the values it can't take.
_msgspec_encoder = msgspec.json.Encoder(enc_hook=lambda array: array.tolist())


def arrow_ipc(frame: pl.DataFrame) -> bytes:
    sink = frame.write_ipc_stream(None)
    return sink.getvalue()


def plotly_bundle(transport: str) -> str:
    return PLOTLY_BUNDLES["typed-dates" if transport == "arrow-ms" else "stock"]


def compress(body: bytes, compression: str) -> tuple[bytes, dict[str, str], float]:
    """Compress a response body; returns (body, extra headers, milliseconds)."""
    if compression == "none":
        return body, {}, 0.0
    level = {"gzip1": 1, "gzip6": 6}[compression]
    start = perf_counter()
    body = gzip.compress(body, compresslevel=level, mtime=0)
    return body, {"Content-Encoding": "gzip"}, (perf_counter() - start) * 1000


def server_timing(**durations_ms: float) -> str:
    """A Server-Timing header, which the browser exposes to the page."""
    return ", ".join(f"{name};dur={ms:.3f}" for name, ms in durations_ms.items())

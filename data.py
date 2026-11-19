"""The dataframes every app and benchmark plots.

A wide time series: one UTC timestamp column plus one value column per trace.
"Points" is the total number of plotted values (rows x traces), so a
10M-point case is 10M rows for one trace or 100k rows for 100 traces.

Two flavours (DATASETS):

* ``scale``: float32, one row per minute, no nulls. The 5k ... 10M sweep.
* ``forecast``: shaped like a dashboard of per-site forecasts: float64, one
  row per half hour, 2% nulls, positive values meant to be stacked. 1,440
  rows is 30 days, with 50 to 500 sites.
"""

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True)
class Dataset:
    dtype: type
    step_minutes: int
    null_fraction: float


DATASETS = {
    "scale": Dataset(np.float32, step_minutes=1, null_fraction=0.0),
    "forecast": Dataset(np.float64, step_minutes=30, null_fraction=0.02),
}
START = np.datetime64("2026-01-01T00:00:00", "ms")

# Choices offered by the interactive apps (any value works from the URL).
PRESET_POINTS = (5_000, 10_000, 50_000, 500_000, 1_000_000, 5_000_000, 10_000_000,
                 16_800, 67_200, 168_000, 72_000, 288_000, 720_000)  # forecast: 336 or 1,440 rows x 50/200/500
PRESET_TRACES = (1, 10, 50, 100, 200, 500)


def make_frame(points: int, traces: int, dataset: str = "scale") -> pl.DataFrame:
    spec = DATASETS[dataset]
    rows = rows_for(points, traces)
    rng = np.random.default_rng(seed=0)
    timestamps = START + np.arange(rows) * np.timedelta64(spec.step_minutes, "m")
    wave = np.sin(np.linspace(0, 20 * np.pi, rows)).astype(spec.dtype)
    columns = {"timestamp": pl.Series(timestamps).dt.replace_time_zone("UTC")}
    for trace in range(traces):
        noise = rng.standard_normal(rows).astype(spec.dtype)
        if dataset == "forecast":  # site loads: positive, so stacking makes sense
            values = 3 + 2 * wave + 0.5 * noise
        else:  # separate bands, so every line is visible
            values = 10 * trace + 5 * wave + noise
        column = pl.Series(values.astype(spec.dtype))
        if spec.null_fraction:
            column = column.scatter(rng.choice(rows, int(rows * spec.null_fraction), replace=False), None)
        columns[trace_name(trace)] = column
    return pl.DataFrame(columns)


def rows_for(points: int, traces: int) -> int:
    if points < 1 or traces < 1 or points % traces:
        raise ValueError(f"{points:,} points do not split evenly into {traces} traces")
    return points // traces


def trace_name(trace: int) -> str:
    return f"trace_{trace:03d}"


class CurrentFrame:
    """Holds the most recent dataframe, standing in for data the app already has.

    Only the dataframe is kept. Every response is encoded from it afresh, so
    nothing about a response is cached.
    """

    def __init__(self, convert=None):
        self.convert = convert or (lambda frame, dataset: frame)
        self.key = None
        self.frame = None

    def load(self, points: int, traces: int, dataset: str = "scale"):
        if self.key != (points, traces, dataset):
            self.frame = None  # free the old frame before building the next one
            self.frame = self.convert(make_frame(points, traces, dataset), dataset)
            self.key = (points, traces, dataset)
        return self.frame

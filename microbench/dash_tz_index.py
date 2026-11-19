"""dash_app.py with the forecast index in Europe/London, tz-aware or not.

Only for the microbenchmark in optimisations.py. Data converted with
tz_convert("Europe/London") has a tz-aware index. With --index naive the app
drops the zone first, the one-line change the microbenchmark measures:

    df.index = df.index.tz_convert("Europe/London").tz_localize(None)

Both draw the same chart: plotly.js shows the wall-clock time of a date string
and ignores its UTC offset.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dash_app  # noqa: E402


def london(frame, dataset, naive):
    df = frame.to_pandas().set_index("timestamp")
    df.index = df.index.tz_convert("Europe/London")
    if naive:
        df.index = df.index.tz_localize(None)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8052)
    parser.add_argument("--index", choices=["tz", "naive"], required=True)
    args = parser.parse_args()
    dash_app.current.convert = lambda frame, dataset: london(frame, dataset, args.index == "naive")
    dash_app.serve(args.port)

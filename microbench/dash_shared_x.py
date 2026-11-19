"""dash_app.py with one change: an array repeated across traces is sent once.

Only for the microbenchmark in optimisations.py. go.Figure copies each trace's
x into its own numpy array, so with 500 traces the same timestamps go over the
wire, and through the renderer's date conversion, 500 times. Here the Arrow
encoder reuses the column of an earlier array with identical contents. The
renderer already maps each placeholder to one decoded array, so it needs no
change.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dash._arrow as dash_arrow  # noqa: E402

import dash_app  # noqa: E402


def add_array_once(arrays, array):
    for index, earlier in enumerate(arrays):
        if earlier.type == array.type and len(earlier) == len(array) and earlier.equals(array):
            return index
    arrays.append(array)
    return len(arrays) - 1


dash_arrow._add_array = add_array_once

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8051)
    dash_app.serve(parser.parse_args().port)

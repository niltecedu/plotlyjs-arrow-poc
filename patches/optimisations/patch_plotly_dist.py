"""Apply plotly-date-typed-array.patch to the prebuilt plotly.min.js.

    python patches/optimisations/patch_plotly_dist.py IN.js OUT.js

The patch is one condition in plotly.js's makeCalcdata. Rebuilding plotly.js
from source just to try it is slow, so this makes the same change to the
minified bundle, and sets Plotly.typedDateAxes so pages can tell which build
they have (web/dates.js and the Dash renderer check it).
"""

import sys
from pathlib import Path

# The minified form of the condition the patch changes (plotly.js 4.3.0).
STOCK = 'Yo.isTypedArray(A)&&(S==="linear"||S==="log")'
PATCHED = 'Yo.isTypedArray(A)&&(S==="linear"||S==="log"||S==="date"&&A instanceof Float64Array)'
FLAG = "\nwindow.Plotly.typedDateAxes = true;\n"


def patch(source: str) -> str:
    if source.count(STOCK) != 1:
        raise SystemExit("plotly.min.js does not contain the expected code; update STOCK for this version")
    return source.replace(STOCK, PATCHED) + FLAG


if __name__ == "__main__":
    source, target = map(Path, sys.argv[1:3])
    target.write_text(patch(source.read_text()))

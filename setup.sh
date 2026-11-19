#!/usr/bin/env bash
# Build everything the apps and benchmarks need. Safe to re-run.
#
#   1. Python 3.15 venv with the pinned packages (stock Dash 4.5.0 wheel).
#   2. Dash 4.5.0 source, patched with patches/dash-arrow.patch, renderer rebuilt.
#   3. The patched files are copied over the installed Dash wheel, plus
#      dash/_json.py from Dash PR #4067 (the msgspec encoder) at a pinned commit.
#   4. Browser bundles for the FastAPI page and the Dash timing asset, plus a
#      plotly.js with patches/optimisations/plotly-date-typed-array.patch.
set -euo pipefail
cd "$(dirname "$0")"

DASH_TAG_COMMIT=9b04a1d0f1d8eeccaf0c2f21dfbc2b1ecaa2dbb9   # v4.5.0
PR_4067_COMMIT=9f262d5341f7129b0bb2369d8acf68005e8835ab     # plotly/dash#4067 head
DASH_SRC=.upstream/dash-4.5.0
RENDERER="$DASH_SRC/dash/dash-renderer"

echo "== Python environment"
[[ -x .venv/bin/python ]] || uv venv --python 3.15 .venv
uv pip install --python .venv/bin/python --reinstall-package dash -r requirements.txt
.venv/bin/playwright install chromium

echo "== Dash 4.5.0 source + Arrow patch"
if [[ ! -d "$DASH_SRC/.git" ]]; then
    git init -q "$DASH_SRC"
    git -C "$DASH_SRC" fetch -q --depth 1 https://github.com/plotly/dash.git "$DASH_TAG_COMMIT"
fi
git -C "$DASH_SRC" -c advice.detachedHead=false checkout -q --force "$DASH_TAG_COMMIT"
git -C "$DASH_SRC" clean -fdq -e node_modules
git -C "$DASH_SRC" apply "$PWD/patches/dash-arrow.patch"
npm install --prefix "$RENDERER" --no-audit --no-fund --loglevel=error
npm run build:js --prefix "$RENDERER" --silent > /dev/null

echo "== Overlay patched Dash onto the installed wheel"
site_dash=$(.venv/bin/python -c 'import dash, os; print(os.path.dirname(dash.__file__))')
# The overlay is only valid if the wheel matches the tag we patched.
git -C "$DASH_SRC" show "$DASH_TAG_COMMIT:dash/_callback.py" | cmp -s - "$site_dash/_callback.py" \
    || { echo "Installed dash/_callback.py does not match Dash 4.5.0" >&2; exit 1; }
cp "$DASH_SRC/dash/_arrow.py" "$DASH_SRC/dash/_callback.py" "$site_dash/"
cp "$RENDERER/build/dash_renderer.min.js" "$RENDERER/build/dash_renderer.dev.js" \
    "$site_dash/dash-renderer/build/"
curl -fsSL -o "$site_dash/_json.py" \
    "https://raw.githubusercontent.com/plotly/dash/$PR_4067_COMMIT/dash/_json.py"

echo "== Browser bundles"
npm ci --no-audit --no-fund --loglevel=error
mkdir -p .build/fastapi .build/dash_assets
for dir in .build/fastapi .build/dash_assets; do
    cp node_modules/plotly.js-dist-min/plotly.min.js "$dir/plotly.min.js"
    .venv/bin/python patches/optimisations/patch_plotly_dist.py \
        node_modules/plotly.js-dist-min/plotly.min.js "$dir/plotly-typed-dates.min.js"
done
cp web/style.css .build/fastapi/style.css
cp web/style.css .build/dash_assets/3-style.css
npx esbuild web/fastapi_page.js --bundle --minify --outfile=.build/fastapi/page.js --log-level=warning
npx esbuild web/dash_timing.js --bundle --minify --outfile=.build/dash_assets/2-timing.js --log-level=warning
npx esbuild microbench/date_axis.js --bundle --minify --outfile=.build/microbench/date_axis.js --log-level=warning

echo "Done. Try: .venv/bin/python fastapi_app.py   or   .venv/bin/python dash_app.py"

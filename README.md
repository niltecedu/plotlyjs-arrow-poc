# Arrow instead of JSON for Plotly.js and Dash graphs

Evidence for the Plotly community thread
[Returning Data as Arrow Instead of Json for Dash Graphs](https://community.plotly.com/t/feature-request-feasiblity-question-returning-data-as-arrow-instead-of-json-for-dash-graphs/97940):
how much faster does a chart appear when its data goes over the wire as an
Arrow IPC stream, rather than JSON from **orjson** (Dash's default today) or
**msgspec** ([Dash PR #4067](https://github.com/plotly/dash/pull/4067))?

This is a proof of concept: a benchmark harness, two small apps, and patches
to Dash and plotly.js that are meant to show the idea and its numbers, not to
be merged as they are.

## How this repo was made

The code, tests and write-up in this repository were generated with AI
models: GPT 6.1 Sol and Claude Opus 5.5. The git history has been rewritten
for cleanliness.

## Summary

- **Arrow on its own (stock plotly.js): the server gets much faster, the
  chart only a little.** Encoding is up to 35x faster than orjson and the
  payload up to 2.7x smaller, but the chart appears only 1.0-1.2x as fast
  (up to 1.5x in Dash with a tz-aware index). Most of the remaining time is
  plotly.js parsing one date string per point.
  [Results](#results-arrow-vs-json).
- **Arrow plus typed dates in plotly.js is the real gain.** With a
  one-condition plotly.js change that lets a date axis take a `Float64Array`,
  large charts appear **1.15x to 3.2x** faster with FastAPI + Plotly.js and
  **1.7x to 4.5x** faster with Dash: for example 12.7 s to 4.1 s for a
  10M-point Dash chart. [Timestamps](#timestamps-the-other-half-of-the-cost).
- **msgspec (PR #4067) is level with orjson for graphs**, since figures still
  go through plotly.py's own array conversion. The PR covers Dash's JSON
  encoding in general, which this POC does not measure.
- **Under about 50k points, no transport makes a visible difference.**

Details below. Every figure, phase and command is in [benchmark.md](benchmark.md):
[quick](benchmark.md#1-quick-benchmark-scale-gzip-level-1), [full](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) and [forecast](benchmark.md#3-forecast-workload-per-site-forecasts) benchmarks, and the
[separate microbenchmarks](benchmark.md#4-optimisations-separate-microbenchmarks).

## What is compared

Two stacks, each written the way such apps usually draw a dataframe:

| | How the chart is built |
| --- | --- |
| **FastAPI + Plotly.js** | an endpoint returns the dataframe's columns; the page builds one trace per column, all sharing one x array |
| **Dash 4.5.0** | a callback builds `go.Figure()` and `add_trace(go.Scatter(x=df.index, y=df[col].values))` from a pandas DataFrame, shown in `dcc.Graph` |

Three transports, plus one with a plotly.js change:

| | FastAPI + Plotly.js | Dash |
| --- | --- | --- |
| **orjson** (baseline) | `orjson.dumps` of the columns as numpy arrays | stock Dash: `to_json_plotly` with orjson |
| **msgspec** | `msgspec.json` with a `tolist()` hook (msgspec has no numpy support) | `dash/_json.py` from PR #4067 at [`9f262d5`](https://github.com/plotly/dash/pull/4067/commits/9f262d5341f7129b0bb2369d8acf68005e8835ab), msgspec engine |
| **arrow** | `DataFrame.write_ipc_stream` | [patches/dash-arrow.patch](patches/dash-arrow.patch), same `go.Figure` |
| **arrow-ms** | the same Arrow response, drawn by plotly.js with [the date patch](patches/optimisations/plotly-date-typed-array.patch) | the same |

The first three run on stock plotly.js 4.3.0. *arrow-ms* is covered in
[Timestamps](#timestamps-the-other-half-of-the-cost).

Two workloads:

- **scale**: float32 values, one row per minute, `scattergl` lines, from 5k
  to 10M points (points = rows x traces) with 1, 10 or 100 traces.
- **forecast**: per-site forecasts. A week (336 rows) or 30 days (1,440 rows)
  of half-hourly float64 values with 2% nulls, for 50, 200 or 500 sites, drawn
  as stacked SVG `scatter` traces. Dash's DataFrame has a tz-aware (UTC) index.

How it is measured:

- **Timing** runs from the browser sending the request to the frame with the
  new plot being **painted**, in a fresh browser context each time, median of
  3. The dataframe is ready on the server first (untimed), and every response
  is encoded from scratch with `Cache-Control: no-store`. WebGL runs on the
  GPU.
- Each transport is tried with no compression, gzip level 1 and gzip level 6.
  Neither Dash nor FastAPI compresses responses by default, so the apps do it
  themselves, with the same code in both.
- The baseline (**1x**) is orjson without compression, for the same app and
  data. `+2.0x` means twice as fast, `-2.0x` twice as slow.
- Every transport must draw the same thing: the runner compares trace count,
  point count and axis ranges, and checks which plotly.js build drew it.
- One laptop (Intel Core Ultra 7 155H, NVIDIA RTX 500 for WebGL, WSL2), server
  and browser on the same machine.

## Results: Arrow vs JSON

Request sent to plot painted, median of 3, no compression, stock plotly.js
4.3.0. Every case, with gzip 1 and 6 and the time split by phase, is in
benchmark.md: [scale](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) and [forecast](benchmark.md#3-forecast-workload-per-site-forecasts).

| Case | orjson (baseline) | msgspec | arrow |
| --- | --- | --- | --- |
| [FastAPI, scale, 10M points x 10 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 8,219 ms | 8,630 ms (-1.05x) | 7,432 ms (+1.11x) |
| [FastAPI, scale, 5M points x 100 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 3,360 ms | 3,272 ms (+1.03x) | 2,726 ms (+1.23x) |
| [FastAPI, forecast, 1,440 rows x 500 sites](benchmark.md#3-forecast-workload-per-site-forecasts) | 1,338 ms | 1,326 ms (+1.01x) | 1,264 ms (+1.06x) |
| [Dash, scale, 10M points x 10 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 12,662 ms | 12,646 ms (same) | 11,101 ms (+1.14x) |
| [Dash, scale, 5M points x 100 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 5,385 ms | 5,247 ms (+1.03x) | 4,408 ms (+1.22x) |
| [Dash, forecast, 1,440 rows x 500 sites](benchmark.md#3-forecast-workload-per-site-forecasts) | 6,712 ms | 7,272 ms (-1.08x) | **4,521 ms (+1.48x)** |
| [Any app, under ~50k points](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | | within noise | within noise |

1. **The server side gets much cheaper.** Arrow encodes 1.6-5.5x faster than
   orjson in FastAPI and 3.5-35x faster in Dash, and the uncompressed payload
   is 2.2-2.7x smaller (no numbers or dates written as text).
2. **The browser side barely moves**, so the chart appears only 1.0-1.2x as
   fast. Stock plotly.js only takes dates as strings, so Arrow's timestamps
   have to become strings in the browser, and plotly.js then parses one per
   point. That parsing is most of the browser's time with any transport.
3. **Dash with a tz-aware index gains more (+1.48x).** plotly.py stores a
   tz-aware index as Python `pd.Timestamp` objects, which orjson encodes
   slowly; the Arrow patch turns them back into one timestamp array.
4. **msgspec (PR #4067) is level with orjson for graphs.** In Dash a figure
   still goes through plotly.py's own array conversion before either encoder
   sees it, so msgspec sends the same bytes as orjson in about the same time
   (0.88-1.03x). The PR covers Dash's JSON encoding in general (layouts,
   components, other props), which this POC does not measure. In FastAPI,
   msgspec has no numpy support, so its JSON is larger and no faster.
5. **gzip shows only its cost here** (mostly 0.6-1.0x): over loopback,
   about 15 Gbit/s, a smaller payload saves almost no download time, so what
   is left is the time to compress, about 0.1 s for a 29 MB response and
   0.8-1.5 s at 10M points. On a real network it pays off. Dividing the bytes
   gzip level 1 saves by the time it takes gives the link speed below which
   it wins: about **0.5-1.7 Gbit/s for JSON**, and 25-500 Mbit/s for Arrow,
   which compresses less. Level 6 makes the payload only 4-12% smaller than
   level 1 but takes 2-8x longer ([benchmark.md section 2](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6)). At 10M
   points x 1 trace the payload is 323 MB of JSON, 73 MB gzipped, 120 MB of
   Arrow, or 69 MB gzipped.
6. **Dash takes longer than FastAPI** for the same data and transport: 1.1-1.6x
   on large charts, 2-5x on forecast charts (flagged in benchmark.md for
   [scale](benchmark.md#dash-vs-fastapi-1) and
   [forecast](benchmark.md#dash-vs-fastapi-2)). Dash
   builds a `go.Figure`, which copies x into every trace, and plotly.py spends
   1.9 s on 500 traces with a tz-aware index before encoding starts.

## Timestamps: the other half of the cost

Stock plotly.js reads a date axis from strings (a plain number is taken as
local-time milliseconds), so to go faster the dates need a different way in.
Four ways to hand plotly.js the timestamps from an Arrow response were
measured on their own, browser only (decode, plot, paint), against JSON
([benchmark.md section 4](benchmark.md#timestamps-four-ways-to-hand-arrow-dates-to-plotlyjs)):

| Way | Forecast, 1,440 x 500 | Scale, 1M x 1 | Same chart as JSON? |
| --- | --- | --- | --- |
| orjson JSON (baseline) | 1,226 ms | 1,000 ms | |
| `toISOString` per value | 1,197 ms (1.02x) | 1,257 ms (-1.26x) | yes |
| **Fast ISO strings** (used by *arrow*) | 1,199 ms (1.02x) | 1,048 ms (0.95x) | yes |
| `Date` objects | 1,005 ms (+1.22x) | 725 ms (+1.38x) | no: shown in the viewer's timezone |
| **UTC ms with the plotly.js patch** (*arrow-ms*) | **934 ms (+1.31x)** | **548 ms (+1.83x)** | yes |
| No timestamps at all, for reference | 925 ms (+1.33x) | 555 ms (+1.80x) | |

- **Fast ISO strings** are what the *arrow* transport and the Dash patch use
  with stock plotly.js: the same strings plotly.py sends today, made with the
  date part once per day and the time part once per second of the day. Charts
  look exactly as before.
- **The plotly.js patch**
  ([plotly-date-typed-array.patch](patches/optimisations/plotly-date-typed-array.patch))
  changes one condition so a date axis takes a `Float64Array` of UTC ms as is,
  with no parsing. That is within a few percent of having no timestamps.
  [patch_plotly_dist.py](patches/optimisations/patch_plotly_dist.py) applies
  the same change to the prebuilt bundle. Upstream it would have to be
  opt-in: a number on a date axis means local time today, and changing that
  would move existing charts.

End to end, with the patch (*arrow-ms*), request sent to plot painted, no
compression ([scale](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6), [forecast](benchmark.md#3-forecast-workload-per-site-forecasts)):

| Case | orjson (baseline) | arrow | arrow-ms |
| --- | --- | --- | --- |
| [FastAPI, scale, 10M points x 10 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 8,219 ms | 7,432 ms (+1.11x) | **3,930 ms (+2.09x)** |
| [FastAPI, scale, 5M points x 100 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 3,360 ms | 2,726 ms (+1.23x) | **1,041 ms (+3.23x)** |
| [FastAPI, forecast, 1,440 rows x 500 sites](benchmark.md#3-forecast-workload-per-site-forecasts) | 1,338 ms | 1,264 ms (+1.06x) | **1,049 ms (+1.28x)** |
| [Dash, scale, 10M points x 10 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 12,662 ms | 11,101 ms (+1.14x) | **4,072 ms (+3.11x)** |
| [Dash, scale, 5M points x 100 traces](benchmark.md#2-full-benchmark-scale-no-compression-gzip-1-gzip-6) | 5,385 ms | 4,408 ms (+1.22x) | **1,200 ms (+4.49x)** |
| [Dash, forecast, 1,440 rows x 500 sites](benchmark.md#3-forecast-workload-per-site-forecasts) | 6,712 ms | 4,521 ms (+1.48x) | **3,931 ms (+1.71x)** |

Across every case from 500k points up, *arrow-ms* is 1.15x to 3.2x faster
with FastAPI and 1.7x to 4.5x faster with Dash.

## Recommendations

1. **Dash: return figure arrays as Arrow**
   ([patches/dash-arrow.patch](patches/dash-arrow.patch); see
   [How the Dash patch works](#how-the-dash-patch-works)). No change to
   callbacks or `dcc.Graph`, much less server time, a smaller payload, and up
   to 1.5x on its own. Most of its value comes with recommendation 2.
2. **plotly.js: accept typed arrays on date axes, as an opt-in**
   ([plotly-date-typed-array.patch](patches/optimisations/plotly-date-typed-array.patch)).
   Together with Arrow: 1.15x to 4.5x on large time-series charts.
3. **Apps that already pass Arrow data to plotly.js by hand:** don't call
   `toISOString` per value. `Date` objects, which I use today, are the fastest
   option on stock plotly.js and right when the chart should show the viewer's
   local time. Fast ISO strings ([web/dates.js](web/dates.js)) keep the data's
   own times instead. The Dash patch uses fast ISO strings, so existing Dash
   charts keep showing the times plotly.py sends.
4. **Compression: gzip level 1 on any real network, or zstd.** For JSON,
   gzip level 1 pays off on links slower than about 0.5-1.7 Gbit/s, so skip
   it only on localhost or a very fast LAN. If you turn on the frameworks'
   own compression, set the level to 1 (`COMPRESS_LEVEL = 1` for
   Flask-Compress, `GZipMiddleware(compresslevel=1)` for Starlette): their
   defaults, 6 and 9, are 2-27x slower for a payload at most 14% smaller.
   zstd level 1, which current browsers accept, is 2-11x faster than gzip
   level 1 and its payload 1-31% smaller
   ([benchmark.md section 4](benchmark.md#compression-the-benchmarks-gzip-framework-defaults-and-zstd)). The benchmarks above use gzip.

Tried and not adopted:

- **Sending x once when traces share it**
  ([microbench/dash_shared_x.py](microbench/dash_shared_x.py),
  [benchmark.md section 4](benchmark.md#dash-send-an-array-repeated-across-traces-once)):
  15% smaller payload, about the same total time.
- **Making the UTC ms on the server**: only a few percent over doing it in the
  browser, and the payload loses the Arrow timestamp type.

## Side finding: tz-aware index in Dash

Outside the scope of the transport work, and optional. The benchmarks above
keep a tz-aware index in Dash. It is noted here because it costs a Dash app
more than any transport choice. plotly.py stores a tz-aware `DatetimeIndex`
as an object array of `pd.Timestamp` in every trace, and every encoder then
walks it in Python. Dropping the zone is one line in the app and draws the
same chart, because plotly.js already ignores a date string's UTC offset:

```python
df.index = df.index.tz_convert("Europe/London").tz_localize(None)
```

Dash, forecast workload, Europe/London index, no compression
([benchmark.md section 4](benchmark.md#dash-app-code-drop-the-timezone-from-the-index-before-plotting),
[microbench/dash_tz_index.py](microbench/dash_tz_index.py)):

| Rows x traces | tz-aware, stock Dash (baseline) | zone dropped, stock Dash | zone dropped, arrow | zone dropped, arrow-ms |
| --- | --- | --- | --- | --- |
| 336 x 50 | 436 ms | 168 ms (+2.6x) | 164 ms (+2.7x) | 153 ms (+2.9x) |
| 1,440 x 50 | 1,504 ms | 370 ms (+4.1x) | 336 ms (+4.5x) | 276 ms (+5.5x) |
| 1,440 x 200 | 5,702 ms | 1,117 ms (+5.1x) | 1,074 ms (+5.3x) | 848 ms (+6.7x) |
| 1,440 x 500 | 14,243 ms | 2,484 ms (+5.7x) | 2,346 ms (+6.1x) | 1,878 ms (+7.6x) |

This is specific to an index (`x=df.index`): a tz-aware column passed as a
Series is already turned into wall-clock `datetime64` by plotly.py, quickly.

## How the Dash patch works

[patches/dash-arrow.patch](patches/dash-arrow.patch) adds 281 lines to Dash
4.5.0 in five files. Existing callbacks need no change, and anything the patch
does not handle goes out as JSON exactly as before.

- **Server** (`dash/_arrow.py`, a hook in `dash/_callback.py`): if the
  renderer accepts `application/vnd.apache.arrow.stream`, every array in a
  returned `figure` that Arrow can carry is replaced by `{"__arrow__": i}`.
  That covers numpy arrays (what `go.Figure` and `px` hold after validating
  their input) and Arrow-backed arrays (Polars Series, pandas Series with
  pyarrow dtypes, pyarrow arrays) in figure dicts. The response is one Arrow
  IPC stream: column `i` holds array `i` (as a one-row list, so lengths can
  differ), and the rest of the response is JSON in the schema metadata. Other
  props (a `dcc.Store`, say) are never touched, nor are types plotly.js
  cannot take (int64), plain lists, background callbacks, or figures with
  animation frames.
- **Timestamps keep plotly.py's meaning**: tz-aware values are sent as
  wall-clock time in their own timezone, which is what plotly.py's JSON
  shows, so the chart shows the same times.
- **Renderer** (`arrowResponse.ts`, `callbacks.ts`): sends the `Accept`
  header, reads the stream with Apache Arrow JS and puts typed arrays back
  where the placeholders were. Numbers arrive as `Float32Array` /
  `Float64Array` views on the response buffer, with no copy; nulls become
  `NaN`. Timestamps become fast ISO strings, or a `Float64Array` of UTC ms
  when plotly.js says it accepts them (`Plotly.typedDateAxes`).
- `dcc.Graph` is unchanged.

## Setup

Requires git, curl, Node.js 24 + npm, and [uv](https://docs.astral.sh/uv/) 0.13 or later.

```sh
bash setup.sh                    # venv, patched Dash, browser bundles (re-runnable)
.venv/bin/python -m pytest tests # unit tests + headless end-to-end check of both apps
```

`setup.sh` creates a Python 3.15 venv with the pinned packages in
[requirements.txt](requirements.txt) and fetches the Dash 4.5.0 source into
`.upstream/`. It applies the patch, rebuilds the renderer, and copies the
changed files over the installed Dash 4.5.0 wheel. It also adds
`dash/_json.py` from PR #4067 at its pinned commit. Stock Dash's own
`to_json` is left alone, so the orjson baseline is stock Dash. Last, it builds
the browser bundles and a patched copy of plotly.js for *arrow-ms*.

## Interactive apps

```sh
.venv/bin/python fastapi_app.py   # http://127.0.0.1:8000
.venv/bin/python dash_app.py      # http://127.0.0.1:8050
```

Pick the workload, transport, compression, points and traces, then **Draw**.
Each draw clears the graph first, and the results table keeps every run with
its speedup against the latest orjson/no-compression draw of the same shape.
**Clear graph** empties the plot. Choosing *arrow-ms* (or leaving it) reloads
the page with the other plotly.js build. Presets can go in the URL, for example
`/?workload=forecast&transport=arrow&compression=gzip1&points=720000&traces=500`.

## Benchmarks

```sh
.venv/bin/python bench.py quick                 # scale, gzip1, 10k / 1M / 10M points
.venv/bin/python bench.py full                  # scale, none / gzip1 / gzip6, 5k ... 10M points
.venv/bin/python bench.py forecast              # forecast, 336 / 1,440 rows x 50 / 200 / 500 sites
.venv/bin/python microbench/optimisations.py    # the separate microbenchmarks (--only <name> for one)
.venv/bin/python report.py                      # results/*.json -> benchmark.md + charts/
```

The runner opens a visible Chromium window, because headless Chromium only has
software WebGL. On WSL2 it sets `GALLIUM_DRIVER=d3d12` and
`MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA` so WebGL runs on the discrete GPU. It
stops if WebGL falls back to a software renderer. `--headless` runs without a
window, but its paint times are not realistic. Results are written after each
app, so an interrupted run keeps what it measured.

**Limits.** One machine over loopback, so there is no network time; a real
link adds transfer time, which favours the smaller payloads (Arrow, gzip).
The values are full-precision random floats, which compress less than typical
real data: with 1-2 decimals, gzipped JSON comes out about the size of
gzipped Arrow. Cases under about 0.5 s vary by 10-20% between samples, so
treat small differences there as noise.

## Files

| File | |
| --- | --- |
| [data.py](data.py) | the two workloads' dataframes |
| [transports.py](transports.py) | FastAPI encoders, gzip, Server-Timing |
| [fastapi_app.py](fastapi_app.py), [web/fastapi_page.js](web/fastapi_page.js) | FastAPI + Plotly.js app |
| [dash_app.py](dash_app.py), [web/dash_timing.js](web/dash_timing.js) | Dash app; routes the encoder per request |
| [web/arrow.js](web/arrow.js), [web/dates.js](web/dates.js) | Arrow columns and timestamps for plotly.js (FastAPI page) |
| [web/common.js](web/common.js) | timing (request sent to paint), results table |
| [plot_style.json](plot_style.json) | traces, layout and config shared by both apps |
| [bench.py](bench.py), [report.py](report.py) | benchmark runner, report generator |
| [microbench/](microbench/) | the separate microbenchmarks and the Dash variants they serve |
| [patches/dash-arrow.patch](patches/dash-arrow.patch) | the Dash change |
| [patches/optimisations/](patches/optimisations/) | the plotly.js date change, and a script applying it to the bundle |
| [results/](results/), [charts/](charts/) | raw results and generated charts |

## License

[MIT](LICENSE). The patches modify [Dash](https://github.com/plotly/dash) and
[plotly.js](https://github.com/plotly/plotly.js), which are also MIT licensed.

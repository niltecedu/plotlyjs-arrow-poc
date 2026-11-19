// Timing and result display shared by the FastAPI page and the Dash app.
//
// A measurement starts when the browser sends the data request and ends when
// the frame showing the new plot has been painted. Phases come from the
// request's Resource Timing entry, so both apps are measured the same way:
//
//   wait      request sent -> first response byte (server work + loopback)
//   download  first byte -> last byte (includes gzip decoding)
//   browser   last byte -> painted (parse/decode, plotly.js, paint)

export const BASELINE = {transport: 'orjson', compression: 'none'};

// Resolves after the next frame has been painted: requestAnimationFrame runs
// just before paint, and a task queued from it runs after.
export function nextPaint() {
    return new Promise(resolve =>
        requestAnimationFrame(() => {
            const channel = new MessageChannel();
            channel.port1.onmessage = () => resolve(performance.now());
            channel.port2.postMessage(null);
        })
    );
}

export function serverTimings(entry) {
    const timings = {};
    let detail = '';
    for (const {name, duration, description} of entry.serverTiming) {
        if (name === 'case') detail = description;
        else timings[`${name}_ms`] = duration;
    }
    return {timings, detail};
}

export function measurement(entry, paintedAt) {
    return {
        total_ms: paintedAt - entry.startTime,
        wait_ms: entry.responseStart - entry.startTime,
        download_ms: entry.responseEnd - entry.responseStart,
        browser_ms: paintedAt - entry.responseEnd,
        wire_bytes: entry.encodedBodySize,
        raw_bytes: entry.decodedBodySize,
        ...serverTimings(entry).timings
    };
}

// What plotly.js ended up drawing; the benchmark checks every transport
// produces the same thing.
export function plotFingerprint(gd) {
    const full = gd._fullLayout;
    return {
        traces: gd._fullData.length,
        points: gd._fullData.reduce((sum, trace) => sum + trace._length, 0),
        xrange: full.xaxis.range.map(String),
        yrange: full.yaxis.range.map(value => Number(value.toPrecision(6)))
    };
}

const history = [];

// Every finished draw goes through here: kept for the benchmark runner on
// window.benchResults and shown in the results table.
export function recordResult(result) {
    // Which plotly.js drew it: the patched build takes typed UTC ms on date axes.
    result.typed_dates = Boolean(window.Plotly?.typedDateAxes);
    history.push(result);
    window.benchResults = history;
    renderHistory(document.getElementById('results'));
}

const fmtMs = ms => (ms === undefined ? '' : ms < 10 ? ms.toFixed(1) : Math.round(ms).toLocaleString());
const fmtBytes = bytes =>
    bytes >= 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${(bytes / 1e3).toFixed(1)} kB`;

function speedup(result) {
    const base = history.findLast(
        other =>
            other.transport === BASELINE.transport &&
            other.compression === BASELINE.compression &&
            other.points === result.points &&
            other.traces === result.traces
    );
    if (!base) return '<span class="muted">draw orjson / none first</span>';
    const ratio = base.total_ms / result.total_ms;
    if (Math.abs(ratio - 1) < 0.05) return '<span class="same">same as baseline</span>';
    return ratio > 1
        ? `<span class="faster">+${ratio.toFixed(2)}x faster</span>`
        : `<span class="slower">-${(1 / ratio).toFixed(2)}x slower</span>`;
}

function renderHistory(element) {
    if (!element) return;
    const rows = history
        .slice()
        .reverse()
        .map(
            r => `<tr>
      <td>${r.transport}</td><td>${r.compression}</td>
      <td class="num">${r.points.toLocaleString()}</td><td class="num">${r.traces}</td>
      <td class="num strong">${fmtMs(r.total_ms)}</td><td>${speedup(r)}</td>
      <td class="num">${fmtMs(r.encode_ms)}</td><td class="num">${fmtMs(r.compress_ms)}</td>
      <td class="num">${fmtMs(r.wait_ms)}</td><td class="num">${fmtMs(r.download_ms)}</td>
      <td class="num">${fmtMs(r.browser_ms)}</td>
      <td class="num">${fmtBytes(r.wire_bytes)}</td><td class="num">${fmtBytes(r.raw_bytes)}</td>
    </tr>`
        )
        .join('');
    element.innerHTML = `
    <p class="muted">Milliseconds from the request being sent to the plot being painted.
      Baseline is orjson without compression at the same points and traces.</p>
    <div class="table-wrap"><table>
      <thead><tr>
        <th>Transport</th><th>Compression</th><th>Points</th><th>Traces</th>
        <th>Total ms</th><th>vs baseline</th><th>Encode</th><th>Gzip</th>
        <th>Wait</th><th>Download</th><th>Browser</th><th>Wire</th><th>Raw</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

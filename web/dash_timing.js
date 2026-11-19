// Dash asset: times each figure callback the same way the FastAPI page does.
//
// The figure response carries a Server-Timing "case" entry (see dash_app.py).
// When plotly.js finishes drawing a non-empty figure, take the newest such
// request, wait for the next paint, and record the result.
import {measurement, nextPaint, plotFingerprint, recordResult, serverTimings} from './common.js';

const reported = new Set();
performance.setResourceTimingBufferSize(100000);

function latestFigureRequest() {
    const entries = performance
        .getEntriesByType('resource')
        .filter(e => e.name.includes('_dash-update-component') && serverTimings(e).detail);
    return entries.at(-1);
}

async function onAfterPlot(gd) {
    const entry = latestFigureRequest();
    if (!gd.data.length || !entry || reported.has(entry.startTime)) return;
    reported.add(entry.startTime);
    const painted = await nextPaint();

    const [workload, transport, compression, points, traces, sent] = serverTimings(entry).detail.split(' ');
    recordResult({
        app: 'dash',
        workload,
        transport,
        compression,
        points: Number(points),
        traces: Number(traces),
        sent,
        ...measurement(entry, painted),
        fingerprint: plotFingerprint(gd)
    });
}

// dcc.Graph renders its plotly.js div asynchronously; attach once it exists.
const watcher = setInterval(() => {
    const gd = document.querySelector('#graph .js-plotly-plot');
    if (!gd || !gd.on) return;
    clearInterval(watcher);
    gd.on('plotly_afterplot', () => onAfterPlot(gd));
    window.dashGraphReady = true;
}, 50);

// arrow-ms needs the patched plotly.js and the others the stock one; the page
// loads one of them (dash_app.py picks it from the URL), so switching reloads.
document.addEventListener('change', event => {
    if (!event.target.closest?.('#transport')) return;
    if ((event.target.value === 'arrow-ms') === Boolean(window.Plotly?.typedDateAxes)) return;
    const params = new URLSearchParams();
    for (const id of ['workload', 'transport', 'compression', 'points', 'traces']) {
        const checked = document.querySelector(`#${id} input:checked`);
        if (checked) params.set(id, checked.value);
    }
    location.search = params.toString();
});

// FastAPI + Plotly.js page: fetch the dataframe's columns, build traces, plot.
import {tableFromIPC} from 'apache-arrow';
import style from '../plot_style.json';
import {toPlotlyArray} from './arrow.js';
import {measurement, nextPaint, plotFingerprint, recordResult} from './common.js';

const graph = document.getElementById('graph');
const drawButton = document.getElementById('draw');
const status = document.getElementById('status');

function controls() {
    const value = id => document.getElementById(id).value;
    return {
        workload: value('workload'),
        transport: value('transport'),
        compression: value('compression'),
        points: Number(value('points')),
        traces: Number(value('traces'))
    };
}

// JSON body: {"timestamp": [iso strings], "trace_000": [numbers], ...}
function columnsFromJson(body) {
    const columns = JSON.parse(new TextDecoder().decode(body));
    const {timestamp, ...traces} = columns;
    return {x: timestamp, traces};
}

// Arrow body: the dataframe as an IPC stream. Float columns without nulls
// become typed-array views on the received buffer.
function columnsFromArrow(body) {
    const table = tableFromIPC(new Uint8Array(body));
    const traces = {};
    for (const field of table.schema.fields.slice(1)) {
        traces[field.name] = toPlotlyArray(table.getChild(field.name));
    }
    return {x: toPlotlyArray(table.getChild('timestamp')), traces};
}

async function draw() {
    const run = controls();
    const query = new URLSearchParams({...run, nonce: crypto.randomUUID()});
    drawButton.disabled = true;
    try {
        // Not timed: start from an empty graph with the dataframe in memory.
        Plotly.purge(graph);
        status.textContent = 'Preparing dataframe...';
        await fetch(`/prepare?${query}`, {method: 'POST'}).then(checkOk);
        await nextPaint();

        status.textContent = 'Drawing...';
        const url = new URL(`/columns?${query}`, location.href).href;
        const response = await fetch(url, {cache: 'no-store'}).then(checkOk);
        const body = await response.arrayBuffer();
        const received = performance.now();

        const {x, traces} =
            run.transport.startsWith('arrow') ? columnsFromArrow(body) : columnsFromJson(body);
        const trace = style.traces[run.workload];
        const data = Object.entries(traces).map(([name, y]) => ({...trace, name, x, y}));
        const decoded = performance.now();

        await Plotly.react(graph, data, style.layout, style.config);
        const plotted = performance.now();
        const painted = await nextPaint();

        const [entry] = performance.getEntriesByName(url, 'resource');
        recordResult({
            app: 'fastapi',
            ...run,
            ...measurement(entry, painted),
            decode_ms: decoded - received,
            plot_ms: plotted - decoded,
            paint_ms: painted - plotted,
            sent: response.headers.get('Content-Type').includes('arrow') ? 'arrow' : 'json',
            fingerprint: plotFingerprint(graph)
        });
        performance.clearResourceTimings();
        status.textContent = '';
    } catch (error) {
        status.textContent = String(error);
        window.benchError = String(error);
    } finally {
        drawButton.disabled = false;
    }
}

async function checkOk(response) {
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response;
}

drawButton.addEventListener('click', draw);
// arrow-ms needs the patched plotly.js and the others the stock one; the page
// loads one of them, so switching between them reloads it.
document.getElementById('transport').addEventListener('change', () => {
    if ((controls().transport === 'arrow-ms') !== Boolean(Plotly.typedDateAxes)) {
        location.search = new URLSearchParams(controls()).toString();
    }
});
document.getElementById('clear').addEventListener('click', () => Plotly.purge(graph));
drawButton.disabled = false;

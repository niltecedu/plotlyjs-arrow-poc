// Browser side of the timestamp microbenchmark (see optimisations.py).
//
// Browser only: decode the payload, build traces, Plotly.react, paint.
// MODES in optimisations.py says which payload and plotly.js build each mode
// uses; the timestamp conversions themselves are web/dates.js.
import {tableFromIPC} from 'apache-arrow';
import style from '../plot_style.json';
import {nextPaint, plotFingerprint} from '../web/common.js';
import {dateObjects, fastIsoDates, isoDates, utcMs} from '../web/dates.js';

const CONVERT = {iso: isoDates, 'fast-iso': fastIsoDates, date: dateObjects, ms: utcMs};

// Null slots hold arbitrary values in Arrow's data buffer; plotly.js shows NaN as a gap.
function values(vector) {
    if (!vector.nullCount) return vector.toArray();
    const out = vector.toArray().slice();
    for (let i = 0; i < out.length; i++) if (!vector.isValid(i)) out[i] = NaN;
    return out;
}

function fromJson(body) {
    const {timestamp, ...columns} = JSON.parse(new TextDecoder().decode(body));
    return {x: timestamp, columns};
}

function fromArrow(body, convert) {
    const table = tableFromIPC(new Uint8Array(body));
    const columns = {};
    let x;
    for (const field of table.schema.fields) {
        const vector = table.getChild(field.name);
        if (field.name === 'timestamp') x = convert(vector);
        else columns[field.name] = values(vector);
    }
    return {x, columns};
}

window.runCase = async ({mode, url, workload}) => {
    const body = await fetch(url).then(response => response.arrayBuffer());
    const graph = document.getElementById('graph');
    await nextPaint();

    const start = performance.now();
    const {x, columns} = url.endsWith('.json') ? fromJson(body) : fromArrow(body, CONVERT[mode]);
    const trace = style.traces[workload];
    // Without timestamps plotly.js uses the row index on a linear axis.
    const data = Object.entries(columns).map(([name, y]) =>
        x === undefined ? {...trace, name, y} : {...trace, name, x, y}
    );
    const layout = x === undefined ? {...style.layout, xaxis: {type: 'linear'}} : style.layout;
    const decoded = performance.now();
    await Plotly.react(graph, data, layout, style.config);
    const plotted = performance.now();
    const painted = await nextPaint();
    return {
        total_ms: painted - start,
        decode_ms: decoded - start,
        plot_ms: plotted - decoded,
        fingerprint: plotFingerprint(graph)
    };
};

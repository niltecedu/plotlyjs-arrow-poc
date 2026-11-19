// Arrow column -> array plotly.js can plot. Same rules as the Dash renderer
// (arrowResponse.ts in patches/dash-arrow.patch).
import {DataType} from 'apache-arrow';
import {plotlyDates} from './dates.js';

export function toPlotlyArray(vector) {
    if (DataType.isTimestamp(vector.type)) return plotlyDates(vector);
    if (!vector.nullCount) return vector.toArray(); // a view on the received buffer, no copy
    if (DataType.isFloat(vector.type)) return nullsAsNaN(vector);
    return Array.from(vector);
}

// Null slots hold arbitrary values in the data buffer; plotly.js shows NaN as a gap.
function nullsAsNaN(vector) {
    const values = vector.toArray().slice();
    for (let i = 0; i < values.length; i++) if (!vector.isValid(i)) values[i] = NaN;
    return values;
}

// Ways to turn an Arrow timestamp column into something plotly.js can plot.
//
// Arrow sends timestamps as int64 (a BigInt64Array in JS). plotly.js reads a
// date axis from ISO strings or Date objects; bare numbers are taken as
// local-time milliseconds, and its typed-array fast path is linear/log only.
// microbench/optimisations.py compares these.

const MS_PER_UNIT = [1e3, 1, 1e-3, 1e-6]; // Arrow TimeUnit: s, ms, us, ns
const DAY_MS = 86_400_000;

function epochMs(vector) {
    const scale = MS_PER_UNIT[vector.type.unit];
    const raw = vector.toArray();
    const ms = new Float64Array(raw.length);
    for (let i = 0; i < raw.length; i++) ms[i] = Number(raw[i]) * scale;
    return ms;
}

// ISO strings, one Date + toISOString per value. Same chart as the JSON path.
export function isoDates(vector) {
    const ms = epochMs(vector);
    const dates = new Array(ms.length);
    for (let i = 0; i < ms.length; i++) dates[i] = new Date(ms[i]).toISOString();
    return dates;
}

// The same ISO strings (minus the trailing Z, which plotly.js ignores) without
// a Date per value: the date part is formatted once per day and the time part
// once per second of the day.
const timeOfDay = [];

function hhmmss(second) {
    const pad = n => (n < 10 ? '0' : '') + n;
    return `${pad(Math.floor(second / 3600))}:${pad(Math.floor(second / 60) % 60)}:${pad(second % 60)}`;
}

export function fastIsoDates(vector) {
    const ms = epochMs(vector);
    const dates = new Array(ms.length);
    let dayStart = NaN;
    let prefix = '';
    for (let i = 0; i < ms.length; i++) {
        const t = ms[i];
        if (!(t >= dayStart && t < dayStart + DAY_MS)) {
            dayStart = Math.floor(t / DAY_MS) * DAY_MS;
            prefix = new Date(dayStart).toISOString().slice(0, 11); // "2026-01-01T"
        }
        const inDay = t - dayStart;
        const second = Math.floor(inDay / 1000);
        const time = (timeOfDay[second] ??= hhmmss(second));
        const millis = Math.round(inDay - second * 1000);
        dates[i] = millis ? `${prefix}${time}.${String(millis).padStart(3, '0')}` : prefix + time;
    }
    return dates;
}

// Date objects, what I use today by hand. plotly.js shows them in the
// browser's local time, so this matches the other modes only when the browser
// runs in the timezone the chart should show.
export function dateObjects(vector) {
    const ms = epochMs(vector);
    const dates = new Array(ms.length);
    for (let i = 0; i < ms.length; i++) dates[i] = new Date(ms[i]);
    return dates;
}

// A Float64Array of UTC ms. Only plotly.js with
// patches/optimisations/plotly-date-typed-array.patch reads these as UTC.
export const utcMs = epochMs;

// What the apps use, chosen with microbench/optimisations.py: UTC ms when
// plotly.js takes them (the patched build sets Plotly.typedDateAxes), else
// fast ISO strings, which draw exactly what the JSON path draws.
export function plotlyDates(vector) {
    return window.Plotly?.typedDateAxes ? utcMs(vector) : fastIsoDates(vector);
}

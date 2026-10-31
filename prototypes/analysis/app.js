/* Spending Analysis prototype: vanilla JS, no dependencies. Reads window.MOCK from data.js. */
(function () {
  const T = MOCK.transactions;
  const TODAY = MOCK.TODAY;
  const $ = (id) => document.getElementById(id);
  const PAL = ['#3b5bdb', '#0ca678', '#f59f00', '#e64980', '#7048e8', '#1098ad', '#f76707', '#74b816', '#d6336c', '#868e96'];
  const SYM = { ILS: '₪', USD: '$', EUR: '€', JPY: '¥' };
  const COUNTRY_NAMES = { IL: 'Israel', JP: 'Japan', IT: 'Italy', US: 'United States', NL: 'Netherlands', SE: 'Sweden' };
  const countryName = (c) => COUNTRY_NAMES[c] || c;
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* ---------- formatting ---------- */
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (n, cur = 'ILS', dec) => {
    const d = dec ?? (cur === 'USD' || cur === 'EUR' ? 2 : 0);
    const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    return (n < 0 && Math.round(Math.abs(n) * 10 ** d) > 0 ? '-' : '') + SYM[cur] + s;
  };
  const ils2 = (n) => money(n, 'ILS', 2);
  const pct = (n) => (n * 100).toFixed(n > 0 && n < 0.1 ? 1 : 0) + '%';
  const bidi = (s, cls = '') => `<span dir="auto" class="${cls}">${esc(s)}</span>`;
  const niceType = (t) => t.charAt(0) + t.slice(1).toLowerCase().replace(/_/g, ' ');

  /* ---------- dates (ISO strings, UTC math) ---------- */
  const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); };
  const fmtD = (d) => d.toISOString().slice(0, 10);
  const addDays = (s, n) => fmtD(new Date(parse(s).getTime() + n * 864e5));
  const addMonths = (s, k) => {
    const d = parse(s), day = d.getUTCDate();
    const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + k, 1));
    const dim = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 0)).getUTCDate();
    t.setUTCDate(Math.min(day, dim));
    return fmtD(t);
  };
  const monthStart = (s) => s.slice(0, 8) + '01';
  const monthEnd = (s) => addDays(addMonths(monthStart(s), 1), -1);
  const isMonthStart = (s) => s.endsWith('-01');
  const isMonthEnd = (s) => monthEnd(s) === s;
  const diffDays = (a, b) => Math.round((parse(b) - parse(a)) / 864e5);
  const weekStart = (s) => addDays(s, -((parse(s).getUTCDay() + 6) % 7));
  const shortDate = (s) => `${+s.slice(8)} ${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}`;

  const PRESETS = {
    last_month: ['Last month', () => { const f = addMonths(monthStart(TODAY), -1); return [f, monthEnd(f)]; }],
    this_month: ['This month so far', () => [monthStart(TODAY), TODAY]],
    last_30: ['Last 30 days', () => [addDays(TODAY, -29), TODAY]],
    last_3m: ['Last 3 months', () => [addMonths(monthStart(TODAY), -2), TODAY]],
    last_12m: ['Last 12 months', () => [addMonths(monthStart(TODAY), -11), TODAY]],
    ytd: ['Year to date', () => [TODAY.slice(0, 4) + '-01-01', TODAY]],
    custom: ['Custom range', null]
  };

  const rangeLabel = (r) => {
    if (isMonthStart(r.from) && isMonthEnd(r.to) && r.from.slice(0, 7) === r.to.slice(0, 7)) return `${MON[+r.from.slice(5, 7) - 1]} ${r.from.slice(0, 4)}`;
    return `${shortDate(r.from)} – ${shortDate(r.to)}`;
  };
  const monthsIn = (r) => {
    if (isMonthStart(r.from) && isMonthEnd(r.to)) {
      return (+r.to.slice(0, 4) - +r.from.slice(0, 4)) * 12 + (+r.to.slice(5, 7) - +r.from.slice(5, 7)) + 1;
    }
    return Math.max(1, (diffDays(r.from, r.to) + 1) / 30.4375);
  };
  const prevRange = (r, mode) => {
    if (mode === 'yoy') return { from: addMonths(r.from, -12), to: addMonths(r.to, -12) };
    if (isMonthStart(r.from) && isMonthEnd(r.to)) {
      const k = Math.round(monthsIn(r));
      return { from: addMonths(r.from, -k), to: addDays(r.from, -1) };
    }
    if (isMonthStart(r.from) && r.from.slice(0, 7) === r.to.slice(0, 7)) { // month-to-date
      const f = addMonths(r.from, -1);
      const t = addMonths(r.to, -1);
      return { from: f, to: t > monthEnd(f) ? monthEnd(f) : t };
    }
    const n = diffDays(r.from, r.to) + 1;
    return { from: addDays(r.from, -n), to: addDays(r.from, -1) };
  };

  /* ---------- dimensions & filters ---------- */
  const bucketKey = (d, g) => (g === 'day' ? d : g === 'week' ? weekStart(d) : g === 'month' ? d.slice(0, 7) : d.slice(0, 4));
  const DIMS = {
    category: { label: 'Category', key: (t) => t.cat },
    custom: { label: 'Custom category', key: (t) => t.custom || '(none)' },
    day: { label: 'Day', period: true, key: (t) => t.date },
    week: { label: 'Week', period: true, key: (t) => weekStart(t.date) },
    month: { label: 'Month', period: true, key: (t) => t.date.slice(0, 7) },
    year: { label: 'Year', period: true, key: (t) => t.date.slice(0, 4) },
    card: { label: 'Card', key: (t) => t.card },
    currency: { label: 'Currency', key: (t) => t.cur },
    merchant: { label: 'Merchant', key: (t) => t.merchant },
    type: { label: 'Transaction type', key: (t) => t.type },
    trip: { label: 'Trip', key: (t) => t.trip || '(no trip)' },
    country: { label: 'Country', key: (t) => t.country }
  };
  const ENUM_FILTERS = ['category', 'custom', 'card', 'currency', 'type', 'trip', 'country'];
  const FILTER_LABELS = { ...Object.fromEntries(ENUM_FILTERS.map((f) => [f, DIMS[f].label])), merchant: 'Merchant contains', min: 'Min amount', max: 'Max amount' };

  const labelOf = (dim, k) => {
    if (dim === 'month') return `${MON[+k.slice(5) - 1]} ${k.slice(0, 4)}`;
    if (dim === 'day') return shortDate(k);
    if (dim === 'week') return `Wk of ${+k.slice(8)} ${MON[+k.slice(5, 7) - 1]}`;
    if (dim === 'type') return niceType(k);
    if (dim === 'country') return countryName(k);
    return k;
  };
  const valueLabel = (field, v) => (field === 'min' || field === 'max' ? money(v) : field === 'type' ? niceType(v) : field === 'country' ? countryName(v) : v);

  function matcher(filters) {
    const by = {};
    filters.forEach((f) => (by[f.field] = by[f.field] || []).push(f.value));
    return (t) => {
      for (const field in by) {
        const v = by[field];
        if (field === 'min') { if (!(t.ils >= v[0])) return false; }
        else if (field === 'max') { if (!(t.ils <= v[0])) return false; }
        else if (field === 'merchant') { if (!v.some((x) => t.merchant.toLowerCase().includes(x.toLowerCase()))) return false; }
        else if (!v.includes(DIMS[field].key(t))) return false;
      }
      return true;
    };
  }
  const rowsFor = (range, filters) => { const m = matcher(filters); return T.filter((t) => t.date >= range.from && t.date <= range.to && m(t)); };

  /* ---------- aggregation ---------- */
  function agg(rows) {
    const a = { rows, sum: 0, count: rows.length, gross: 0, pc: 0, refunds: 0, orig: {} };
    rows.forEach((t) => {
      a.sum += t.ils;
      if (t.ils > 0) { a.gross += t.ils; a.pc++; } else a.refunds += t.ils;
      a.orig[t.cur] = (a.orig[t.cur] || 0) + t.orig;
    });
    return a;
  }
  function groupRows(rows, dim) {
    const m = new Map();
    rows.forEach((t) => { const k = DIMS[dim].key(t); if (!m.has(k)) m.set(k, []); m.get(k).push(t); });
    const arr = [...m].map(([key, rs]) => ({ key, label: labelOf(dim, key), ...agg(rs) }));
    return arr.sort(DIMS[dim].period ? (a, b) => (a.key < b.key ? -1 : 1) : (a, b) => b.sum - a.sum);
  }
  const buckets = (r, g) => {
    const out = [];
    for (let d = r.from; d <= r.to; d = addDays(d, 1)) { const k = bucketKey(d, g); if (out[out.length - 1] !== k) out.push(k); }
    return out;
  };
  const origText = (a) => {
    const foreign = Object.keys(a.orig).filter((c) => c !== 'ILS');
    if (!foreign.length) return '—';
    return foreign.length === 1 && !a.orig.ILS ? money(a.orig[foreign[0]], foreign[0]) : 'mixed';
  };
  const topN = (groups, n) => {
    const pos = groups.filter((g) => g.sum > 0);
    const shown = pos.slice(0, n), rest = pos.slice(n);
    const other = rest.length ? { key: '__other', label: `Other (${rest.length})`, sum: rest.reduce((s, g) => s + g.sum, 0), keys: rest.map((g) => g.key) } : null;
    return { shown, other };
  };

  /* ---------- state ---------- */
  const state = { preset: 'last_month', from: '', to: '', compare: 'none', groupBy: 'category', gran: 'auto', view: 'bar', filters: [], drill: null, sel: null, showAvg: true, flash: false };
  const DEFAULTS = { compare: 'none', groupBy: 'category', gran: 'auto', view: 'bar', filters: [] };
  const SCENARIOS = [
    ['Last month by category', { preset: 'last_month' }],
    ['All JPY spending (Japan trip)', { preset: 'custom', from: '2025-12-01', to: '2026-01-31', filters: [{ field: 'currency', value: 'JPY' }], groupBy: 'merchant' }],
    ['Petrol, last 12 months', { preset: 'last_12m', groupBy: 'month', view: 'trend', filters: [{ field: 'category', value: 'דלק חשמל וגז' }] }],
    ['Last month vs last year', { preset: 'last_month', compare: 'yoy' }],
    ['Category × month heatmap', { preset: 'last_12m', view: 'heat' }]
  ];
  function applyState(patch) {
    Object.assign(state, JSON.parse(JSON.stringify(DEFAULTS)), patch, { drill: null, sel: null });
    if (state.preset !== 'custom') [state.from, state.to] = PRESETS[state.preset][1]();
  }

  /* ---------- derived context ---------- */
  let reg = []; // drill registry, rebuilt every render
  const drill = (cons) => reg.push({ cons }) - 1;
  const dAttr = (cons) => `data-d="${drill(cons)}"`;

  function context() {
    const range = { from: state.from, to: state.to };
    const rows = rowsFor(range, state.filters);
    const span = diffDays(range.from, range.to) + 1;
    const pDim = DIMS[state.groupBy].period;
    const c = { range, rows, span, total: agg(rows), pDim, flash: state.flash };
    state.flash = false;
    c.gran = state.gran !== 'auto' ? state.gran : span <= 45 ? 'day' : span <= 120 ? 'week' : 'month';
    c.mgran = state.gran !== 'auto' ? state.gran : span <= 120 ? 'week' : 'month';
    c.sdim = pDim ? 'category' : state.groupBy; // dimension used as series/rows by views that already use time on an axis
    c.groups = groupRows(rows, state.groupBy);
    if (state.compare !== 'none') {
      c.prevRange = prevRange(range, state.compare);
      c.prevRows = rowsFor(c.prevRange, state.filters);
      c.prevTotal = agg(c.prevRows);
      if (!pDim) {
        c.prevMap = new Map(groupRows(c.prevRows, state.groupBy).map((g) => [g.key, g]));
        const have = new Set(c.groups.map((g) => g.key));
        c.prevMap.forEach((g, k) => { if (!have.has(k)) c.groups.push({ key: k, label: g.label, ...agg([]) }); });
      }
    }
    c.color = new Map();
    c.groups.forEach((g, i) => c.color.set(g.key, PAL[i % PAL.length]));
    return c;
  }
  const drillGroup = (c, g, dim = state.groupBy) => dAttr([{ dim, keys: g.keys || [g.key] }]);

  /* ---------- stat cards ---------- */
  function delta(cur, prev, fmt) {
    const d = cur - prev;
    if (Math.abs(d) < 0.005) return '<span class="delta flat">no change</span>';
    const p = prev ? ` (${d > 0 ? '+' : '−'}${Math.abs((d / prev) * 100).toFixed(1)}%)` : ' (new)';
    return `<span class="delta ${d > 0 ? 'up' : 'down'}">${d > 0 ? '▲' : '▼'} ${fmt(Math.abs(d))}${p}</span>`;
  }
  function renderStats(c) {
    const a = c.total, p = c.prevTotal;
    const am = avgInfo(c.rows, c.range, 'month'), pam = p && avgInfo(c.prevRows, c.prevRange, 'month');
    const avgP = (x) => (x.pc ? x.gross / x.pc : 0);
    const amSub = am.fallback ? 'no complete month yet · includes partial' : `over ${am.n} complete month${am.n === 1 ? '' : 's'}${am.partial.length ? ` · excl. ${partialNames(am, 'month')} (partial)` : ''}`;
    const cards = [
      ['Net spending', money(a.sum), `refunds netted: ${money(a.refunds)}`, p && delta(a.sum, p.sum, money)],
      ['Transactions', a.count, `${a.pc} purchases · ${a.count - a.pc} refunds`, p && delta(a.count, p.count, (n) => n)],
      ['Avg per purchase', money(avgP(a)), 'gross purchases ÷ purchase count', p && delta(avgP(a), avgP(p), money)],
      ['Avg per month', money(am.avg), amSub, pam && delta(am.avg, pam.avg, money), 'data-act="avgcard" title="Show the average line on the trend chart"']
    ];
    const foreign = Object.keys(a.orig).filter((x) => x !== 'ILS');
    if (a.count && foreign.length === 1 && !a.orig.ILS) cards.splice(1, 0, [`Original amount (${foreign[0]})`, money(a.orig[foreign[0]], foreign[0]), `≈ ${money(a.sum)} billed in ILS`, p && ''] );
    $('stats').innerHTML = cards.map(([l, v, s, d, attr]) => `<div class="stat${attr ? ' clickable' : ''}" ${attr || ''}><div class="label">${l}</div><div class="value num" style="text-align:left">${v}</div><div class="sub">${s}</div>${d ? `<div style="margin-top:6px">${d}</div>` : ''}</div>`).join('') +
      (p ? `<div class="cmpnote">Compared with ${rangeLabel(c.prevRange)} (${p.count} transactions, net ${money(p.sum)}). Red = spending went up, green = went down.</div>` : '');
  }

  /* ---------- views ---------- */
  const noData = '<div class="empty">No transactions match the current period and filters.</div>';

  function viewBar(c) {
    const list = c.groups.slice(0, 25);
    const ai = c.pDim && state.showAvg ? avgInfo(c.rows, c.range, state.groupBy) : null;
    const max = Math.max(1, ai ? ai.avg : 0, ...list.map((g) => Math.max(g.sum, c.prevMap?.get(g.key)?.sum || 0)));
    const tot = c.total.gross || 1;
    const mark = ai ? `<div class="avg-mark ${c.flash ? 'avg-flash' : ''}" style="left:${Math.max(0, ai.avg) / max * 100}%"></div>` : '';
    return (ai ? `<div class="plot-key"><span><b style="color:#e8590c">┆</b> ${avgLabel(ai)} (net, complete ${ai.unit}s only)</span>${ai.partial.length ? `<span>Faded bar = partial ${ai.unit} (${partialNames(ai, ai.unit)}), excluded</span>` : ''}</div>` : '') + '<div class="bars">' + list.map((g) => {
      const pv = c.prevMap?.get(g.key), part = ai && ai.partial.includes(g.key);
      return `<div class="bar-row" ${drillGroup(c, g)} ${ai ? `title="${esc(g.label)}: ${vsAvg(g.sum, ai.avg)}${part ? ' · partial, not in average' : ''}"` : ''}>
        <div class="bar-label" title="${esc(g.label)}">${bidi(g.label)}</div>
        <div class="bar-track">${mark}<div class="bar-fill" style="width:${Math.max(0, g.sum) / max * 100}%;background:${c.color.get(g.key)};${part ? 'opacity:.45' : ''}"></div>${c.prevMap ? `<div class="bar-ghost" title="Previous: ${money(pv?.sum || 0)}" style="width:${Math.max(0, pv?.sum || 0) / max * 100}%"></div>` : ''}</div>
        <div class="bar-val ${g.sum < 0 ? 'neg' : ''}">${money(g.sum)}<small>${g.sum > 0 ? pct(g.sum / tot) + ' · ' : ''}${g.count} txn</small></div></div>`;
    }).join('') + (c.groups.length > 25 ? `<div class="muted small" style="padding:8px">Showing top 25 of ${c.groups.length}; see the table below for all.</div>` : '') +
      (c.prevMap ? '<div class="muted small" style="padding:8px 6px 0">Thin grey bar = previous period</div>' : '') + '</div>';
  }

  function viewDonut(c) {
    const { shown, other } = topN(c.groups, 8);
    const items = other ? [...shown, other] : shown;
    const total = items.reduce((s, g) => s + g.sum, 0);
    if (!total) return noData;
    const R = 95, r = 58, p = (a, rad) => [100 + rad * Math.sin(a), 100 - rad * Math.cos(a)];
    let a0 = 0;
    const paths = items.map((g, i) => {
      const a1 = a0 + Math.min(0.9999, g.sum / total) * Math.PI * 2;
      const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R), [x2, y2] = p(a1, r), [x3, y3] = p(a0, r);
      const lg = a1 - a0 > Math.PI ? 1 : 0;
      const d = `M${x0} ${y0}A${R} ${R} 0 ${lg} 1 ${x1} ${y1}L${x2} ${y2}A${r} ${r} 0 ${lg} 0 ${x3} ${y3}Z`;
      a0 = a1;
      return `<path d="${d}" fill="${g.key === '__other' ? '#ced4e4' : c.color.get(g.key)}" ${drillGroup(c, g)}><title>${esc(g.label)}: ${money(g.sum)}</title></path>`;
    }).join('');
    const legend = items.map((g) => `<div class="legend-item" ${drillGroup(c, g)}><span class="dot" style="background:${g.key === '__other' ? '#ced4e4' : c.color.get(g.key)}"></span>${bidi(g.label)}<span class="num">${money(g.sum)}</span><span class="num muted">${pct(g.sum / total)}</span></div>`).join('');
    return `<div class="donut-wrap"><svg viewBox="0 0 200 200">${paths}<text x="100" y="97" text-anchor="middle" style="font-size:9px">Gross spend</text><text x="100" y="114" text-anchor="middle" style="font-size:15px;font-weight:600;fill:var(--text)">${money(total)}</text></svg><div class="legend">${legend}</div></div>`;
  }

  const niceMax = (v) => { if (v <= 0) return 1; const p = 10 ** Math.floor(Math.log10(v)), f = v / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };
  const axisLabel = (g, k) => (g === 'month' ? MON[+k.slice(5) - 1] + " '" + k.slice(2, 4) : g === 'day' ? `${+k.slice(8)} ${MON[+k.slice(5, 7) - 1]}` : g === 'week' ? `${+k.slice(8)} ${MON[+k.slice(5, 7) - 1]}` : k);
  const sumBy = (rows, g) => { const m = {}; rows.forEach((t) => { const k = bucketKey(t.date, g); m[k] = (m[k] || 0) + t.ils; }); return m; };

  /* Average over complete buckets only: a bucket counts if it lies fully inside the range and has ended (not after TODAY).
     Refunds are netted because sums are net ILS. Empty buckets count as zero. */
  const bucketBounds = (k, g) => (g === 'day' ? [k, k] : g === 'week' ? [k, addDays(k, 6)] : g === 'month' ? [k + '-01', monthEnd(k + '-01')] : [k + '-01-01', k + '-12-31']);
  function avgInfo(rows, range, g) {
    const bs = buckets(range, g), sums = sumBy(rows, g), limit = range.to < TODAY ? range.to : TODAY;
    const done = bs.filter((b) => { const [s, e] = bucketBounds(b, g); return s >= range.from && e <= limit; });
    const used = done.length ? done : bs;
    const total = used.reduce((s, b) => s + (sums[b] || 0), 0);
    return { avg: used.length ? total / used.length : 0, n: used.length, unit: g, fallback: !done.length, partial: done.length ? bs.filter((b) => !done.includes(b)) : [] };
  }
  const partialNames = (ai, g) => ai.partial.slice(0, 2).map((b) => labelOf(g, b)).join(', ') + (ai.partial.length > 2 ? ` +${ai.partial.length - 2}` : '');
  const avgLabel = (ai, prefix = 'Avg') => `${prefix} ${money(ai.avg)} / ${ai.unit}`;
  const vsAvg = (v, avg) => { const d = v - avg; return `${d >= 0 ? '+' : '−'}${money(Math.abs(d))}${avg ? ` (${d >= 0 ? '+' : '−'}${Math.abs((d / avg) * 100).toFixed(0)}%)` : ''} vs avg`; };
  const avgLineSvg = (yy, label, x1, x2, color, cls, anchorEnd = true, dy = -6) =>
    `<g class="${cls}"><line x1="${x1}" x2="${x2}" y1="${yy}" y2="${yy}" stroke="${color}" stroke-width="1.6" stroke-dasharray="6 4"/><text class="halo" x="${anchorEnd ? x2 - 4 : x1 + 4}" y="${yy + dy}" text-anchor="${anchorEnd ? 'end' : 'start'}" style="fill:${color};font-weight:600">${label}</text></g>`;

  function viewTrend(c) {
    const g = c.gran, bs = buckets(c.range, g), sums = sumBy(c.rows, g);
    const vals = bs.map((b) => sums[b] || 0);
    let pvals = null;
    if (c.prevRows) { const pb = buckets(c.prevRange, g), ps = sumBy(c.prevRows, g); pvals = pb.map((b) => ps[b] || 0); }
    const show = state.showAvg, ai = avgInfo(c.rows, c.range, g), pai = c.prevRows && avgInfo(c.prevRows, c.prevRange, g);
    const avgs = show ? [ai.avg, ...(pai ? [pai.avg] : [])] : [];
    const W = 800, H = 300, L = 68, R = 16, Tp = 14, B = 34;
    const lo = Math.min(0, ...vals, ...(pvals || [])), hi = niceMax(Math.max(...vals, ...(pvals || []), ...avgs, 0));
    const n = Math.max(bs.length, pvals ? pvals.length : 0);
    const x = (i) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1));
    const y = (v) => Tp + (H - Tp - B) * (1 - (v - lo) / (hi - lo));
    const line = (arr) => arr.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');
    const grid = [0, 1, 2, 3, 4].map((i) => { const v = lo + ((hi - lo) * i) / 4; return `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${money(v)}</text>`; }).join('');
    const step = Math.ceil(bs.length / 10);
    const xl = bs.map((b, i) => (i % step ? '' : `<text x="${x(i)}" y="${H - 12}" text-anchor="middle">${axisLabel(g, b)}</text>`)).join('');
    const pts = vals.map((v, i) => {
      const part = ai.partial.includes(bs[i]);
      const fill = !show ? '#fff' : part ? '#fff' : v > ai.avg ? 'var(--danger)' : 'var(--success)';
      const stroke = show && !part ? fill : 'var(--primary)';
      const tip = `${labelOf(g, bs[i])}: ${money(v)}${show ? ` · ${vsAvg(v, ai.avg)}` : ''}${part ? ' · partial, not in average' : ''}`;
      return `<circle class="pt" cx="${x(i)}" cy="${y(v)}" r="${part ? 5 : 4.5}" fill="${fill}" stroke="${stroke}" stroke-width="2" ${part ? 'stroke-dasharray="2 2"' : ''} ${dAttr([{ dim: g, keys: [bs[i]] }])}><title>${tip}</title></circle>`;
    }).join('');
    const area = bs.length > 1 ? `<path d="${line(vals)}L${x(bs.length - 1)} ${y(0)}L${x(0)} ${y(0)}Z" fill="var(--primary)" opacity=".08"/>` : '';
    const partialLast = bs.length > 1 && ai.partial.includes(bs[bs.length - 1]);
    const main = partialLast
      ? `<path d="${line(vals.slice(0, -1))}" fill="none" stroke="var(--primary)" stroke-width="2.5"/><path d="M${x(bs.length - 2)} ${y(vals[bs.length - 2])}L${x(bs.length - 1)} ${y(vals[bs.length - 1])}" fill="none" stroke="var(--primary)" stroke-width="2.5" stroke-dasharray="4 4"/>`
      : `<path d="${line(vals)}" fill="none" stroke="var(--primary)" stroke-width="2.5"/>`;
    const flash = c.flash ? 'avg-flash' : '';
    const avgSvg = show ? (pai ? avgLineSvg(y(pai.avg), avgLabel(pai, 'Prev avg'), L, W - R, '#9aa3c0', 'avg-line', false, 14) : '') + avgLineSvg(y(ai.avg), avgLabel(ai), L, W - R, '#e8590c', `avg-line ${flash}`) : '';
    const hint = show && ai.partial.length ? `<span>◌ Dashed point = partial ${g} (${partialNames(ai, g)}), excluded from the average</span>` : '';
    const key = show ? '<span><b style="color:var(--danger)">●</b> above avg · <b style="color:var(--success)">●</b> below avg</span>' : '';
    return `<div class="plot-key"><span><i style="background:var(--primary)"></i>${rangeLabel(c.range)}</span>${pvals ? `<span><i style="background:#adb5d0"></i>${rangeLabel(c.prevRange)}</span>` : ''}<span>Bucket: ${g}</span>${key}${hint}</div>
      <svg class="plot" viewBox="0 0 ${W} ${H}">${grid}${xl}${pvals ? `<path d="${line(pvals)}" fill="none" stroke="#adb5d0" stroke-width="2" stroke-dasharray="5 4"/>` : ''}${area}${main}${avgSvg}${pts}</svg>`;
  }

  function viewStacked(c) {
    const g = c.gran, bs = buckets(c.range, g), dim = c.sdim;
    const gl = groupRows(c.rows, dim), { shown, other } = topN(gl, 7);
    const col = new Map(shown.map((s, i) => [s.key, PAL[i]]));
    const series = other ? [...shown, other] : shown;
    const slot = (k) => (col.has(k) ? k : '__other');
    const cell = {};
    c.rows.forEach((t) => { if (t.ils <= 0) return; const b = bucketKey(t.date, g), s = slot(DIMS[dim].key(t)); (cell[b] = cell[b] || {})[s] = (cell[b][s] || 0) + t.ils; });
    const totals = bs.map((b) => Object.values(cell[b] || {}).reduce((s, v) => s + v, 0));
    const show = state.showAvg, ai = avgInfo(c.rows, c.range, g), net = sumBy(c.rows, g);
    const W = 800, H = 300, L = 68, R = 10, Tp = 14, B = 34, hi = niceMax(Math.max(...totals, ...(show ? [ai.avg] : []), 0));
    const bw = (W - L - R) / bs.length, y = (v) => Tp + (H - Tp - B) * (1 - v / hi);
    const grid = [0, 1, 2, 3, 4].map((i) => { const v = (hi * i) / 4; return `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${money(v)}</text>`; }).join('');
    const step = Math.ceil(bs.length / 12);
    let svg = '';
    bs.forEach((b, i) => {
      let acc = 0;
      series.forEach((s) => {
        const v = (cell[b] || {})[s.key] || 0;
        if (!v) return;
        const keys = s.keys || [s.key];
        const part = show && ai.partial.includes(b);
        svg += `<rect class="seg" ${part ? 'opacity=".5"' : ''} x="${L + i * bw + bw * 0.12}" width="${bw * 0.76}" y="${y(acc + v)}" height="${y(acc) - y(acc + v)}" fill="${s.key === '__other' ? '#ced4e4' : col.get(s.key)}" ${dAttr([{ dim: g, keys: [b] }, { dim, keys }])}><title>${labelOf(g, b)} · ${esc(s.label)}: ${money(v)}${show ? ` · bucket total ${money(net[b] || 0)} (${vsAvg(net[b] || 0, ai.avg)})${part ? ', partial' : ''}` : ''}</title></rect>`;
        acc += v;
      });
      if (!(i % step)) svg += `<text x="${L + i * bw + bw / 2}" y="${H - 12}" text-anchor="middle" ${dAttr([{ dim: g, keys: [b] }])} style="cursor:pointer">${axisLabel(g, b)}</text>`;
    });
    const legend = series.map((s) => `<span ${dAttr([{ dim, keys: s.keys || [s.key] }])}><i class="dot" style="background:${s.key === '__other' ? '#ced4e4' : col.get(s.key)}"></i>${bidi(s.label)}</span>`).join('');
    const avgSvg = show ? avgLineSvg(y(ai.avg), avgLabel(ai), L, W - R, '#e8590c', `avg-line ${c.flash ? 'avg-flash' : ''}`) : '';
    const hint = show && ai.partial.length ? `<div class="muted small" style="margin-top:6px">Faded bar = partial ${g} (${partialNames(ai, g)}), excluded from the average (net of refunds). Stacks show purchases only; the average line is net.</div>` : '';
    return `<svg class="plot" viewBox="0 0 ${W} ${H}">${grid}${svg}${avgSvg}</svg><div class="legend-inline">${legend}</div>${hint}${DIMS[state.groupBy].period ? `<div class="muted small" style="margin-top:6px">Stacked by Category because the time axis already uses the ${DIMS[state.groupBy].label.toLowerCase()} grouping. Refunds are excluded from stacks.</div>` : ''}`;
  }

  function matrix(c) {
    const cols = buckets(c.range, c.mgran), dim = c.sdim, rows = groupRows(c.rows, dim);
    const cell = {}, colTot = {};
    c.rows.forEach((t) => { const k = DIMS[dim].key(t) + '\u0000' + bucketKey(t.date, c.mgran); cell[k] = (cell[k] || 0) + t.ils; });
    cols.forEach((b) => { colTot[b] = c.rows.filter((t) => bucketKey(t.date, c.mgran) === b).reduce((s, t) => s + t.ils, 0); });
    return { cols, rows, cell, colTot, dim, g: c.mgran };
  }
  const colHead = (m, b) => `<th class="num" ${dAttr([{ dim: m.g, keys: [b] }])}>${axisLabel(m.g, b)}</th>`;

  function viewHeat(c) {
    const m = matrix(c), rows = m.rows.slice(0, 20);
    const max = Math.max(1, ...rows.flatMap((r) => m.cols.map((b) => m.cell[r.key + '\u0000' + b] || 0)));
    const body = rows.map((r) => `<tr><td class="rowhead" ${drillGroup(c, r, m.dim)}>${bidi(r.label)}</td>${m.cols.map((b) => {
      const v = m.cell[r.key + '\u0000' + b];
      const bg = v > 0 ? `rgba(59,91,219,${(0.08 + 0.85 * v / max).toFixed(2)})` : v < 0 ? 'rgba(47,158,68,.25)' : '#f6f7fb';
      return `<td class="cell" style="background:${bg};color:${v > 0 && v / max > 0.55 ? '#fff' : 'var(--text)'}" ${v === undefined ? '' : dAttr([{ dim: m.dim, keys: [r.key] }, { dim: m.g, keys: [b] }])} title="${esc(r.label)} · ${labelOf(m.g, b)}">${v === undefined ? '' : money(v)}</td>`;
    }).join('')}</tr>`).join('');
    return `<div class="table-wrap"><table class="heat"><thead><tr><th>${DIMS[m.dim].label}</th>${m.cols.map((b) => colHead(m, b)).join('')}</tr></thead><tbody>${body}</tbody></table></div>${m.rows.length > 20 ? `<div class="muted small" style="padding-top:8px">Top 20 of ${m.rows.length} shown.</div>` : ''}`;
  }

  function viewPivot(c) {
    const m = matrix(c);
    const body = m.rows.map((r) => `<tr><td class="rowhead" ${drillGroup(c, r, m.dim)}>${bidi(r.label)}</td>${m.cols.map((b) => {
      const v = m.cell[r.key + '\u0000' + b];
      return v === undefined ? '<td class="num muted">·</td>' : `<td class="num ${v < 0 ? 'neg' : ''}" data-d="${drill([{ dim: m.dim, keys: [r.key] }, { dim: m.g, keys: [b] }])}">${money(v)}</td>`;
    }).join('')}<td class="num"><b>${money(r.sum)}</b></td></tr>`).join('');
    const foot = `<tr><td>Total</td>${m.cols.map((b) => `<td class="num">${money(m.colTot[b])}</td>`).join('')}<td class="num">${money(c.total.sum)}</td></tr>`;
    return `<div class="table-wrap"><table class="pivot"><thead><tr><th>${DIMS[m.dim].label}</th>${m.cols.map((b) => colHead(m, b)).join('')}<th class="num">Total</th></tr></thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table></div>`;
  }

  const VIEWS = { bar: ['Bar by group', viewBar], donut: ['Donut', viewDonut], trend: ['Trend line', viewTrend], stacked: ['Stacked bar', viewStacked], heat: ['Heatmap', viewHeat], pivot: ['Pivot table', viewPivot] };

  /* ---------- breakdown table ---------- */
  function renderTable(c) {
    const cmp = !!c.prevMap, a = c.total, tot = a.gross || 1;
    $('table-title').textContent = `Breakdown by ${DIMS[state.groupBy].label.toLowerCase()}`;
    if (!c.groups.length) { $('table').innerHTML = noData; return; }
    const head = `<tr><th>${DIMS[state.groupBy].label}</th><th class="num">Net (ILS)</th><th class="num">Share</th><th class="num">Txns</th><th class="num">Avg / purchase</th><th class="num">Original</th>${cmp ? '<th class="num">Previous</th><th class="num">Δ ILS</th><th class="num">Δ %</th>' : ''}</tr>`;
    const body = c.groups.map((g) => {
      let extra = '';
      if (cmp) {
        const pv = c.prevMap.get(g.key)?.sum || 0, d = g.sum - pv;
        extra = `<td class="num">${money(pv)}</td><td class="num ${d > 0 ? 'neg' : ''}" style="${d < 0 ? 'color:var(--success)' : ''}">${d > 0 ? '+' : d < 0 ? '−' : ''}${money(Math.abs(d))}</td><td class="num">${pv ? (d > 0 ? '+' : d < 0 ? '−' : '') + Math.abs(d / pv * 100).toFixed(1) + '%' : 'new'}</td>`;
      }
      return `<tr ${drillGroup(c, g)}><td>${bidi(g.label)}</td><td class="num ${g.sum < 0 ? 'neg' : ''}">${money(g.sum)}</td><td class="num muted">${g.sum > 0 ? pct(g.sum / tot) : '—'}</td><td class="num">${g.count}</td><td class="num">${g.pc ? money(g.gross / g.pc) : '—'}</td><td class="num">${origText(g)}</td>${extra}</tr>`;
    }).join('');
    let foot = `<tr><td>Total</td><td class="num">${money(a.sum)}</td><td></td><td class="num">${a.count}</td><td class="num">${a.pc ? money(a.gross / a.pc) : '—'}</td><td class="num">${origText(a)}</td>`;
    if (cmp) { const d = a.sum - c.prevTotal.sum; foot += `<td class="num">${money(c.prevTotal.sum)}</td><td class="num">${d > 0 ? '+' : d < 0 ? '−' : ''}${money(Math.abs(d))}</td><td class="num">${c.prevTotal.sum ? (d > 0 ? '+' : d < 0 ? '−' : '') + Math.abs(d / c.prevTotal.sum * 100).toFixed(1) + '%' : 'new'}</td>`; }
    $('table').innerHTML = `<table><thead>${head}</thead><tbody>${body}</tbody><tfoot>${foot}</tr></tfoot></table>` +
      (cmp && c.pDim ? '<div class="muted small" style="padding-top:8px">Per-row comparison is not shown when grouping by time; see the totals row and stat cards.</div>' : '');
  }

  /* ---------- filter chips & controls ---------- */
  function renderChips() {
    const range = `<span class="chip static">Date: <b>${rangeLabel({ from: state.from, to: state.to })}</b></span>`;
    $('chips').innerHTML = range + state.filters.map((f, i) => {
      const v = valueLabel(f.field, f.value);
      return `<span class="chip">${FILTER_LABELS[f.field]}: <b dir="auto">${esc(v)}</b><button data-rm="${i}" title="Remove filter">×</button></span>`;
    }).join('') + (state.filters.length ? '<button class="btn pill" data-act="clear">Clear all</button>' : '');
  }
  function optionsFor(field) {
    const dim = DIMS[field];
    return [...new Set(T.map(dim.key))].sort((a, b) => a.localeCompare(b)).map((v) => `<option value="${esc(v)}">${esc(valueLabel(field, v))}</option>`).join('');
  }
  function buildFilterInput() {
    const f = $('f-field').value;
    $('f-input').innerHTML = ENUM_FILTERS.includes(f) ? `<select id="f-val" dir="auto">${optionsFor(f)}</select>` :
      f === 'merchant' ? '<input type="text" id="f-val" placeholder="e.g. פז, Amazon" dir="auto">' : '<input type="number" id="f-val" placeholder="ILS" step="1">';
  }
  function addFilter() {
    const f = $('f-field').value, el = $('f-val');
    let v = el.value.trim();
    if (v === '') return;
    if (f === 'min' || f === 'max') { v = Number(v); state.filters = state.filters.filter((x) => x.field !== f); }
    if (!state.filters.some((x) => x.field === f && x.value === v)) state.filters.push({ field: f, value: v });
    if (el.tagName === 'INPUT') el.value = '';
    render();
  }
  function initControls() {
    $('preset').innerHTML = Object.entries(PRESETS).map(([k, v]) => `<option value="${k}">${v[0]}</option>`).join('');
    $('groupBy').innerHTML = Object.entries(DIMS).map(([k, v]) => `<option value="${k}">${v.period ? 'Period: ' : ''}${v.label}</option>`).join('');
    $('f-field').innerHTML = Object.entries(FILTER_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
    $('scenarios').innerHTML = SCENARIOS.map(([l], i) => `<button class="btn pill" data-scn="${i}">${l}</button>`).join('');
    $('views').innerHTML = Object.entries(VIEWS).map(([k, v]) => `<button data-view="${k}">${v[0]}</button>`).join('');
    buildFilterInput();
    $('f-field').onchange = buildFilterInput;
    $('f-add').onclick = addFilter;
    $('f-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') addFilter(); });
    $('preset').onchange = (e) => { state.preset = e.target.value; if (state.preset !== 'custom') [state.from, state.to] = PRESETS[state.preset][1](); render(); };
    const dateChange = () => { if ($('from').value && $('to').value && $('from').value <= $('to').value) { state.from = $('from').value; state.to = $('to').value; state.preset = 'custom'; } render(); };
    $('from').onchange = $('to').onchange = dateChange;
    ['compare', 'groupBy', 'gran'].forEach((k) => { $(k).onchange = (e) => { state[k] = e.target.value; render(); }; });
    $('showAvg').onchange = (e) => { state.showAvg = e.target.checked; render(); };
  }
  function syncControls() {
    $('preset').value = state.preset; $('from').value = state.from; $('to').value = state.to;
    ['compare', 'groupBy', 'gran'].forEach((k) => { $(k).value = state[k]; });
    $('showAvg').checked = state.showAvg;
    $('avg-toggle').hidden = !(state.view === 'trend' || state.view === 'stacked' || (state.view === 'bar' && DIMS[state.groupBy].period));
    document.querySelectorAll('[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  }

  /* ---------- drawer ---------- */
  function createCustom(t) {
    const name = (prompt('New custom category name (1–60 characters)') || '').trim();
    if (!name) return;
    if (name.length > 60) { alert('Name must be 1–60 characters'); return; }
    if (MOCK.CUSTOM_CATEGORIES.some((c) => c.toLowerCase() === name.toLowerCase())) { alert('A category with this name already exists'); return; }
    MOCK.CUSTOM_CATEGORIES.push(name); t.custom = name;
  }
  const conLabel = (con) => `${DIMS[con.dim].label}: ${con.keys.length > 2 ? `${con.keys.length} values` : con.keys.map((k) => labelOf(con.dim, k)).join(', ')}`;
  function drillRows() {
    const rows = rowsFor({ from: state.from, to: state.to }, state.filters);
    return rows.filter((t) => state.drill.cons.every((con) => con.keys.includes(DIMS[con.dim].key(t))));
  }
  const tagCat = (t) => `${bidi(t.cat, 'tag')}${t.custom ? ' ' + bidi(t.custom, 'tag custom') : ''}`;

  function renderDrawer() {
    const dr = $('drawer'), ov = $('overlay');
    const open = !!state.drill;
    dr.hidden = ov.hidden = !open;
    document.body.style.overflow = open ? 'hidden' : '';
    if (!open) { dr.innerHTML = ''; return; }
    const rows = drillRows().slice().sort((a, b) => (a.date < b.date ? 1 : -1));
    if (state.sel) {
      const t = T.find((x) => x.id === state.sel);
      const opts = ['', ...MOCK.CUSTOM_CATEGORIES].map((o) => `<option value="${esc(o)}" ${o === t.custom ? 'selected' : ''}>${o || '(none)'}</option>`).join('') + '<option value="__new">Create new…</option>';
      dr.innerHTML = `<div class="drawer-head"><div class="row"><button class="btn" data-act="back">← Back to list</button><button class="icon-btn" data-act="close" title="Close">×</button></div></div>
        <div class="drawer-body"><div class="detail"><div class="muted small">${t.date}${t.inst ? ' · installment ' + t.inst : ''}</div>
        <div style="font-size:20px;font-weight:600">${bidi(t.merchant)}</div>
        <div class="amount ${t.ils < 0 ? 'neg' : ''}">${ils2(t.ils)}</div>
        ${t.cur !== 'ILS' ? `<div class="muted">${money(t.orig, t.cur, t.cur === 'JPY' ? 0 : 2)} original · implied rate ${(t.ils / t.orig).toFixed(4)}</div>` : ''}
        <dl><dt>Transaction date</dt><dd>${t.date}</dd><dt>Charge date</dt><dd>${t.chargeDate}</dd><dt>Merchant country</dt><dd>${esc(countryName(t.country))}</dd>
        <dt>Bank category</dt><dd>${bidi(t.cat)}</dd>
        <dt>Custom category</dt><dd><select id="custom-sel" dir="auto">${opts}</select><div class="muted small">Your override; the bank category above is never changed.</div></dd>
        <dt>Card</dt><dd>${esc(t.card)}</dd><dt>Type</dt><dd>${niceType(t.type)}${t.inst ? ' (' + t.inst + ')' : ''}</dd><dt>Trip</dt><dd>${t.trip ? esc(t.trip) : '—'}</dd><dt>ID</dt><dd class="muted">${t.id}</dd></dl></div></div>`;
      $('custom-sel').onchange = (e) => { if (e.target.value === '__new') createCustom(t); else t.custom = e.target.value; render(); };
      return;
    }
    const a = agg(rows), canFilter = state.drill.cons.some((x) => !DIMS[x.dim].period);
    dr.innerHTML = `<div class="drawer-head"><div class="row"><h2>Transactions (${rows.length})</h2><button class="icon-btn" data-act="close" title="Close (Esc)">×</button></div>
      <div class="chips">${state.drill.cons.map((x) => `<span class="chip static" style="background:var(--primary-soft);color:var(--primary)">${esc(conLabel(x))}</span>`).join('')}
        ${state.filters.map((f) => `<span class="chip static">${FILTER_LABELS[f.field]}: ${esc(valueLabel(f.field, f.value))}</span>`).join('')}
        <span class="chip static">Date: ${rangeLabel({ from: state.from, to: state.to })}</span></div>
      <div class="drawer-sum"><div><b>${money(a.sum)}</b>net</div><div><b>${a.count}</b>transactions</div><div><b>${a.pc ? money(a.gross / a.pc) : '—'}</b>avg / purchase</div><div><b>${origText(a)}</b>original</div>
        ${canFilter ? '<div style="margin-left:auto;align-self:center"><button class="btn" data-act="tofilter">Add as filter</button></div>' : ''}</div></div>
      <div class="drawer-body">${rows.length ? `<table><thead><tr><th>Date</th><th>Merchant</th><th>Category</th><th class="num">ILS</th><th class="num">Original</th></tr></thead><tbody>${rows.map((t) =>
        `<tr class="tx-row" data-tx="${t.id}"><td>${t.date.slice(5)}<span class="muted small"> ${t.date.slice(0, 4)}</span></td><td>${bidi(t.merchant)}${t.inst ? ` <span class="tag">${t.inst}</span>` : ''}</td><td>${tagCat(t)}</td><td class="num ${t.ils < 0 ? 'neg' : ''}">${ils2(t.ils)}</td><td class="num muted">${t.cur === 'ILS' ? '—' : money(t.orig, t.cur, t.cur === 'JPY' ? 0 : 2)}</td></tr>`).join('')}</tbody></table>` : noData}</div>`;
  }

  /* ---------- main render ---------- */
  function render() {
    reg = [];
    syncControls();
    renderChips();
    const c = context();
    renderStats(c);
    $('chart-title').textContent = `Net spending by ${DIMS[state.groupBy].label.toLowerCase()} · ${rangeLabel(c.range)}`;
    $('chart').innerHTML = c.rows.length ? VIEWS[state.view][1](c) : noData;
    renderTable(c);
    renderDrawer();
  }

  /* ---------- events ---------- */
  document.addEventListener('click', (e) => {
    const el = (sel) => e.target.closest(sel);
    let t;
    if ((t = el('[data-tx]'))) { state.sel = t.dataset.tx; renderDrawer(); }
    else if ((t = el('[data-d]'))) { state.drill = reg[+t.dataset.d]; state.sel = null; renderDrawer(); }
    else if ((t = el('[data-rm]'))) { state.filters.splice(+t.dataset.rm, 1); render(); }
    else if ((t = el('[data-view]'))) { state.view = t.dataset.view; render(); }
    else if ((t = el('[data-scn]'))) { applyState(SCENARIOS[+t.dataset.scn][1]); render(); }
    else if ((t = el('[data-act]'))) {
      const act = t.dataset.act;
      if (act === 'clear') { state.filters = []; render(); }
      else if (act === 'avgcard') {
        state.view = 'trend'; state.showAvg = true; state.flash = true;
        if (state.gran !== 'month') state.gran = 'month'; // the card is a monthly average
        render();
        $('chart').scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      else if (act === 'close') { state.drill = state.sel = null; renderDrawer(); }
      else if (act === 'back') { state.sel = null; renderDrawer(); }
      else if (act === 'tofilter') {
        state.drill.cons.filter((x) => !DIMS[x.dim].period).forEach((x) => x.keys.forEach((k) => {
          const f = x.dim === 'merchant' ? { field: 'merchant', value: k } : { field: x.dim, value: k };
          if (!state.filters.some((o) => o.field === f.field && o.value === f.value)) state.filters.push(f);
        }));
        state.drill = state.sel = null; render();
      }
    } else if (e.target === $('overlay')) { state.drill = state.sel = null; renderDrawer(); }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !state.drill) return;
    if (state.sel) state.sel = null; else state.drill = null;
    renderDrawer();
  });

  initControls();
  applyState(SCENARIOS[0][1]);
  render();
})();

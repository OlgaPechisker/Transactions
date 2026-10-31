/* Transactions + Trips prototype. Plain vanilla JS, hash routing. */
(function () {
  const { transactions: TX, trips: TRIPS, cards: CARDS, addDays, dayDiff } = window.DATA;
  const $ = s => document.querySelector(s);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const SYM = { ILS: '₪', USD: '$', EUR: '€', JPY: '¥' };
  const CURRENCIES = ['ILS', 'USD', 'EUR', 'JPY'];
  const TYPES = ['REGULAR', 'RECURRING', 'IMMEDIATE_CHARGE', 'INSTALLMENTS'];

  /* ---------- client-side i18n (the API returns Hebrew bank names and ISO country codes) ---------- */
  const CAT_EN = {
    'מזון וצריכה': 'Food', 'מסעדות קפה וברים': 'Dining & cafes', 'פנאי בידור וספורט': 'Leisure & sport', 'טיסות ותיירות': 'Travel & tourism',
    'תחבורה ורכבים': 'Transport & cars', 'דלק חשמל וגז': 'Fuel, electricity & gas', 'שונות': 'Misc'
  };
  const COUNTRY_NAMES = { IL: 'Israel', JP: 'Japan', IT: 'Italy', US: 'United States', CN: 'China' };
  const countryName = c => COUNTRY_NAMES[c] || c;
  const REVIEW_TEXT = { UNKNOWN_CURRENCY: 'Original currency unknown – pending validation' };
  const REVIEW_URL = '../review/index.html';
  let toastTimer;
  const toast = m => { const t = $('#toast'); t.textContent = m; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3000); };

  /* ---------- formatting ---------- */
  const nf = (n, d) => Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtILS = n => (n < 0 ? '-' : '') + '₪' + nf(n, 2);
  const fmtFx = (cur, n) => (n < 0 ? '-' : '') + (SYM[cur] || '') + nf(n, cur === 'JPY' ? 0 : 2);
  const fmtFxCompact = (cur, n) => Math.abs(n) >= 1e6 ? (n < 0 ? '-' : '') + (SYM[cur] || '') + (Math.abs(n) / 1e6).toFixed(2).replace(/0$/, '') + 'M' : fmtFx(cur, n);
  const fmtDate = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) + '/' + s.slice(0, 4) : '';
  const fmtMonth = ym => MONTHS[+ym.slice(5, 7) - 1] + ' ' + ym.slice(0, 4);
  const amt = n => `<span class="${n < 0 ? 'neg' : ''}">${fmtILS(n)}</span>`;

  /* ---------- domain helpers ---------- */
  const byId = id => TX.find(t => t.id === id);
  const tripById = id => TRIPS.find(t => t.id === id);
  const effCat = t => t.customCategory || t.bankCategory;
  const catEn = c => CAT_EN[c] || c;
  const resolveCategory = v => {
    if (!v) return v;
    const l = v.toLowerCase();
    const k = Object.keys(CAT_EN).find(h => h === v || CAT_EN[h].toLowerCase() === l || CAT_EN[h].toLowerCase().startsWith(l));
    return k || v;
  };
  const catChipLabel = c => (CAT_EN[c] ? CAT_EN[c] + ' (' + c + ')' : c);
  const createdCustoms = [];
  const customCats = () => [...new Set(TX.map(t => t.customCategory).filter(Boolean).concat(createdCustoms))].sort();

  /* Single-trip model (Q-12): the earliest-starting same-currency trip covering the date claims the auto member (tie -> earliest created). */
  const inRange = (tr, t) => t.txDate >= tr.start && t.txDate <= tr.end;
  const isAutoMatch = (tr, t) => t.origCurrency === tr.currency && inRange(tr, t);
  const autoClaimer = t => TRIPS.map((tr, i) => ({ tr, i })).filter(x => isAutoMatch(x.tr, t))
    .sort((a, b) => a.tr.start.localeCompare(b.tr.start) || a.i - b.i).map(x => x.tr)[0] || null;
  const isManual = (tr, t) => t.tripTag === tr.id;
  const isExcluded = (tr, t) => !t.tripTag && autoClaimer(t) === tr && tr.excluded.includes(t.id);
  const tripOf = t => t.tripTag ? tripById(t.tripTag) : (c => c && !c.excluded.includes(t.id) ? c : null)(autoClaimer(t));
  const isMember = (tr, t) => tripOf(t) === tr;
  const tripBadge = t => { const tr = tripOf(t); return tr ? `<span class="badge info">${esc(tr.name)}</span> <span class="muted">${isManual(tr, t) ? 'tagged' : 'auto'}</span>` : ''; };
  function tagTrip(t, tr) {
    const prev = tripOf(t);
    TRIPS.forEach(x => { x.excluded = x.excluded.filter(i => i !== t.id); });
    t.tripTag = tr.id;
    if (prev && prev !== tr) toast('Moved from ' + prev.name + ' to ' + tr.name);
  }
  function setCustomCategory(t, v) {
    if (v === '__new') {
      const name = (prompt('New custom category name (1–60 characters)') || '').trim();
      if (!name) return;
      if (name.length > 60) { toast('Name must be 1–60 characters'); return; }
      if (customCats().some(c => c.toLowerCase() === name.toLowerCase())) { toast('A category with this name already exists'); return; }
      createdCustoms.push(name); t.customCategory = name;
    } else t.customCategory = v === '__clear' ? null : v;
  }

  function totals(rows) {
    const r = { ils: 0, foreign: {}, unknown: 0, commission: 0, count: rows.length };
    rows.forEach(t => {
      r.ils += t.amount; r.commission += t.commission;
      if (t.origCurrency === 'unknown') r.unknown++;
      else if (t.origCurrency !== 'ILS') r.foreign[t.origCurrency] = (r.foreign[t.origCurrency] || 0) + t.origAmount;
    });
    return r;
  }
  const foreignText = (f, compact) => Object.keys(f).map(c => (compact ? fmtFxCompact : fmtFx)(c, f[c])).join(' · ');

  /* ---------- state ---------- */
  const ui = {
    cols: null, sort: { key: 'date', dir: -1 }, group: 'none', collapsed: new Set(),
    openId: null, draftField: 'category', tripSearch: ''
  };

  /* ---------- filters ---------- */
  const optList = a => a.map(v => ({ v, l: v }));
  const FDEFS = [
    { id: 'range', label: 'Date range', keys: ['from', 'to'], inputs: [{ k: 'from', t: 'date', ph: 'From' }, { k: 'to', t: 'date', ph: 'To' }], any: true,
      text: p => 'Date: ' + (p.from ? fmtDate(p.from) : '…') + ' – ' + (p.to ? fmtDate(p.to) : '…') },
    { id: 'month', label: 'Month', keys: ['month'], inputs: [{ k: 'month', t: 'month' }], text: p => 'Month: ' + fmtMonth(p.month) },
    { id: 'category', label: 'Category', keys: ['category'], inputs: [{ k: 'category', t: 'select', opts: () => Object.keys(CAT_EN).map(h => ({ v: h, l: catChipLabel(h) })) }],
      text: p => 'Category: ' + catChipLabel(resolveCategory(p.category)) },
    { id: 'custom', label: 'Custom category', keys: ['custom'], inputs: [{ k: 'custom', t: 'select', opts: () => [{ v: '__none', l: '(no override)' }].concat(optList(customCats())) }],
      text: p => 'Custom: ' + (p.custom === '__none' ? 'none' : p.custom) },
    { id: 'card', label: 'Card', keys: ['card'], inputs: [{ k: 'card', t: 'select', opts: () => optList(CARDS) }], text: p => 'Card: ' + p.card },
    { id: 'currency', label: 'Currency', keys: ['currency'], inputs: [{ k: 'currency', t: 'select', opts: () => optList(CURRENCIES.concat('unknown')) }], text: p => 'Currency: ' + p.currency },
    { id: 'amount', label: 'Amount range (₪)', keys: ['min', 'max'], inputs: [{ k: 'min', t: 'number', ph: 'Min' }, { k: 'max', t: 'number', ph: 'Max' }],
      text: p => 'Amount: ' + (p.min !== undefined ? '₪' + p.min : '…') + ' – ' + (p.max !== undefined ? '₪' + p.max : '…') },
    { id: 'q', label: 'Merchant contains', keys: ['q'], inputs: [{ k: 'q', t: 'text', ph: 'e.g. שופרסל' }], text: p => 'Merchant contains: “' + p.q + '”' },
    { id: 'type', label: 'Type', keys: ['type'], inputs: [{ k: 'type', t: 'select', opts: () => optList(TYPES) }], text: p => 'Type: ' + p.type },
    { id: 'country', label: 'Country', keys: ['country'], inputs: [{ k: 'country', t: 'select', opts: () => [...new Set(TX.map(t => t.country))].sort().map(c => ({ v: c, l: countryName(c) + ' (' + c + ')' })) }], text: p => 'Country: ' + countryName(p.country) },
    { id: 'trip', label: 'Trip', keys: ['trip'], inputs: [{ k: 'trip', t: 'select', opts: () => [{ v: '__none', l: '(no trip)' }].concat(TRIPS.map(t => ({ v: t.id, l: t.name }))) }],
      text: p => 'Trip: ' + (p.trip === '__none' ? 'none' : tripById(p.trip) ? tripById(p.trip).name : p.trip) },
    { id: 'review', label: 'Needs review (unknown currency)', keys: ['review'], inputs: [{ k: 'review', t: 'fixed', value: '1' }], text: () => 'Needs review: unknown currency' }
  ];
  const ALL_KEYS = FDEFS.reduce((a, d) => a.concat(d.keys), []);

  function matches(t, p) {
    if (p.from && t.txDate < p.from) return false;
    if (p.to && t.txDate > p.to) return false;
    if (p.month && !t.txDate.startsWith(p.month)) return false;
    if (p.category && effCat(t) !== resolveCategory(p.category)) return false;
    if (p.custom && (p.custom === '__none' ? t.customCategory : t.customCategory !== p.custom)) return false;
    if (p.card && t.card !== p.card) return false;
    if (p.currency && t.origCurrency !== p.currency) return false;
    if (p.min !== undefined && p.min !== '' && t.amount < +p.min) return false;
    if (p.max !== undefined && p.max !== '' && t.amount > +p.max) return false;
    if (p.q && !t.merchant.toLowerCase().includes(p.q.toLowerCase())) return false;
    if (p.type && t.type !== p.type) return false;
    if (p.country && t.country !== p.country.toUpperCase()) return false;
    if (p.trip === '__none') { if (tripOf(t)) return false; } else if (p.trip) { const tr = tripById(p.trip); if (!tr || !isMember(tr, t)) return false; }
    if (p.review && !t.reviewReasons.includes('UNKNOWN_CURRENCY')) return false;
    return true;
  }

  /* ---------- columns / grouping ---------- */
  const typeBadge = t => {
    if (t.type === 'INSTALLMENTS') return `<span class="badge info">Installments ${t.installment.n}/${t.installment.total}</span>`;
    if (t.type === 'RECURRING') return '<span class="badge info">Recurring</span>';
    if (t.type === 'IMMEDIATE_CHARGE') return '<span class="badge warn">Immediate</span>';
    return '<span class="badge">Regular</span>';
  };
  const origText = t => t.origCurrency === 'unknown' ? '<span class="badge warn">unknown</span>' : t.origCurrency === 'ILS' ? '<span class="muted">—</span>' : fmtFx(t.origCurrency, t.origAmount);
  const COLS = [
    { k: 'date', l: 'Date', on: 1, get: t => t.txDate, r: t => fmtDate(t.txDate), cls: 'nowrap' },
    { k: 'charge', l: 'Charge date', get: t => t.chargeDate, r: t => fmtDate(t.chargeDate), cls: 'nowrap' },
    { k: 'merchant', l: 'Merchant', on: 1, get: t => t.merchant, r: t => `<span dir="auto">${esc(t.merchant)}</span>`, cls: 'text-cell' },
    { k: 'country', l: 'Country', get: t => countryName(t.country), r: t => esc(countryName(t.country)) },
    { k: 'city', l: 'City', get: t => t.city, r: t => `<span dir="auto">${esc(t.city)}</span>` },
    { k: 'category', l: 'Category', on: 1, get: effCat, r: t => `<span dir="auto" title="${esc(catEn(effCat(t)))}">${esc(effCat(t))}</span>${t.customCategory ? '<span class="dot" title="Custom category override"></span>' : ''}`, cls: 'nowrap' },
    { k: 'bank', l: 'Bank category', get: t => t.bankCategory, r: t => `<span dir="auto">${esc(t.bankCategory)}</span>`, cls: 'nowrap' },
    { k: 'card', l: 'Card', on: 1, get: t => t.card, r: t => esc(t.card), cls: 'nowrap' },
    { k: 'type', l: 'Type', on: 1, get: t => t.type, r: typeBadge },
    { k: 'orig', l: 'Original', on: 1, num: 1, get: t => t.origAmount == null ? 0 : t.origAmount, r: origText },
    { k: 'amount', l: 'Billing (₪)', on: 1, num: 1, get: t => t.amount, r: t => amt(t.amount) },
    { k: 'rate', l: 'Eff. rate', num: 1, get: t => t.effectiveRate || 0, r: t => t.origCurrency === 'ILS' || t.origCurrency === 'unknown' ? '<span class="muted">—</span>' : t.effectiveRate.toFixed(4) },
    { k: 'commission', l: 'Commission', num: 1, get: t => t.commission, r: t => t.commission ? fmtILS(t.commission) : '<span class="muted">—</span>' },
    { k: 'trip', l: 'Trip', on: 1, get: t => (tripOf(t) || { name: '' }).name, r: tripBadge },
    { k: 'notes', l: 'Notes', get: t => t.notes, r: t => `<span dir="auto">${esc(t.notes)}</span>`, cls: 'text-cell' },
    { k: 'review', l: 'Review', on: 1, get: t => t.reviewReasons.length, r: t => t.reviewReasons.length ? `<span class="badge warn" title="${esc(t.reviewReasons.map(r => REVIEW_TEXT[r]).join('; '))}">⚠ ${t.reviewReasons.length}</span>` : '' }
  ];
  ui.cols = new Set(COLS.filter(c => c.on).map(c => c.k));

  const GROUPS = {
    none: null,
    category: { l: 'Category', key: effCat, label: k => `<span dir="auto">${esc(k)}</span> <span class="muted">${esc(CAT_EN[k] || '')}</span>` },
    period: { l: 'Period (month)', key: t => t.txDate.slice(0, 7), label: k => fmtMonth(k), desc: true },
    card: { l: 'Card', key: t => t.card, label: esc },
    currency: { l: 'Currency', key: t => t.origCurrency, label: esc },
    merchant: { l: 'Merchant', key: t => t.merchant, label: k => `<span dir="auto">${esc(k)}</span>` },
    type: { l: 'Type', key: t => t.type, label: esc },
    trip: { l: 'Trip', key: t => (tripOf(t) || { name: 'No trip' }).name, label: esc }
  };

  /* ---------- routing ---------- */
  function route() {
    const h = location.hash.replace(/^#/, '') || '/transactions';
    const [path, qs] = h.split('?');
    return { path, params: Object.fromEntries(new URLSearchParams(qs || '')) };
  }
  function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
  function setTxParams(set, remove) {
    const r = route();
    const p = new URLSearchParams(r.params);
    (remove || []).forEach(k => p.delete(k));
    Object.keys(set || {}).forEach(k => p.set(k, set[k]));
    const q = p.toString();
    go('#/transactions' + (q ? '?' + q : ''));
  }

  /* ---------- transactions view ---------- */
  function breadcrumb(p) {
    const parts = [];
    if (p.category) parts.push(esc(catEn(resolveCategory(p.category))));
    if (p.trip && tripById(p.trip)) parts.push(esc(tripById(p.trip).name));
    if (p.month) parts.push(fmtMonth(p.month));
    if (!parts.length) return '<div class="crumbs">Transactions</div>';
    return '<div class="crumbs"><a href="#/analysis">Analysis</a>' + parts.map((x, i) => `<span class="sep">›</span>${i === parts.length - 1 ? '<b>' + x + '</b>' : x}`).join('') + '</div>';
  }

  function sortRows(rows) {
    const col = COLS.find(c => c.k === ui.sort.key) || COLS[0];
    return rows.slice().sort((a, b) => {
      const x = col.get(a), y = col.get(b);
      const c = typeof x === 'number' ? x - y : String(x).localeCompare(String(y));
      return (c || a.id.localeCompare(b.id)) * ui.sort.dir;
    });
  }

  function filterBar(p) {
    const chips = FDEFS.filter(d => d.keys.some(k => p[k] !== undefined && p[k] !== '')).map(d =>
      `<span class="chip">${esc(d.text(p))}<button data-act="rmFilter" data-keys="${d.keys.join(',')}" title="Remove filter">×</button></span>`);
    const def = FDEFS.find(d => d.id === ui.draftField) || FDEFS[2];
    const inputs = def.inputs.map(i => {
      if (i.t === 'fixed') return '';
      if (i.t === 'select') return `<select data-fin="${i.k}">${i.opts().map(o => `<option value="${esc(o.v)}">${esc(o.l)}</option>`).join('')}</select>`;
      return `<input data-fin="${i.k}" type="${i.t}" placeholder="${esc(i.ph || '')}" ${i.t === 'number' ? 'style="width:90px"' : ''}>`;
    }).join('');
    return `<div class="filterbar">${chips.join('')}
      <div class="addfilter"><span class="muted">+ Filter</span>
        <select id="ffield">${FDEFS.map(d => `<option value="${d.id}" ${d.id === def.id ? 'selected' : ''}>${d.label}</option>`).join('')}</select>
        ${inputs}<button class="btn sm primary" data-act="addFilter">Add</button></div>
      ${chips.length ? '<button class="btn sm" data-act="clearFilters">Clear all</button>' : ''}</div>`;
  }

  function renderTransactions(p) {
    const rows = sortRows(TX.filter(t => matches(t, p)));
    const cols = COLS.filter(c => ui.cols.has(c.k));
    const g = GROUPS[ui.group];
    let body = '';
    const rowHtml = t => `<tr class="row ${ui.openId === t.id ? 'selected' : ''}" data-act="openTx" data-id="${t.id}">${cols.map(c => `<td class="${c.cls || ''} ${c.num ? 'num' : ''}">${c.r(t)}</td>`).join('')}</tr>`;
    if (!rows.length) body = `<tr><td colspan="${cols.length}"><div class="empty">No transactions match these filters.</div></td></tr>`;
    else if (!g) body = rows.map(rowHtml).join('');
    else {
      const map = new Map();
      rows.forEach(t => { const k = g.key(t); if (!map.has(k)) map.set(k, []); map.get(k).push(t); });
      const keys = [...map.keys()];
      if (g.desc !== undefined) keys.sort().reverse();
      else keys.sort((a, b) => Math.abs(totals(map.get(b)).ils) - Math.abs(totals(map.get(a)).ils));
      keys.forEach(k => {
        const list = map.get(k), tt = totals(list), closed = ui.collapsed.has(k);
        body += `<tr class="group" data-act="toggleGroup" data-key="${esc(k)}"><td colspan="${cols.length}"><span class="caret">${closed ? '▸' : '▾'}</span>${g.label(k)}
          <span class="muted" style="font-weight:400"> · ${tt.count} tx</span>
          <span style="float:right">${amt(tt.ils)}${Object.keys(tt.foreign).length ? ' <span class="muted" style="font-weight:400">(' + foreignText(tt.foreign, true) + ')</span>' : ''}</span></td></tr>`;
        if (!closed) body += list.map(rowHtml).join('');
      });
    }
    const head = cols.map(c => `<th class="sortable ${c.num ? 'num' : ''}" data-act="sort" data-key="${c.k}">${c.l}${ui.sort.key === c.k ? (ui.sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('');
    const tt = totals(rows);
    const foot = `<tr><td colspan="${cols.length}"><div style="display:flex;gap:28px;flex-wrap:wrap;justify-content:flex-end;align-items:baseline">
      <span class="muted" style="font-weight:400">${tt.count} transactions${tt.unknown ? ' · ' + tt.unknown + ' with unknown currency' : ''}</span>
      ${Object.keys(tt.foreign).map(c => `<span title="Foreign total in original currency">${c}: ${fmtFx(c, tt.foreign[c])}</span>`).join('')}
      ${tt.commission ? `<span class="muted" style="font-weight:400">Commission ${fmtILS(tt.commission)}</span>` : ''}
      <span style="font-size:16px">Net billed: ${amt(tt.ils)}</span></div></td></tr>`;
    const title = p.category || p.month || p.trip ? 'Transactions' : 'All transactions';

    return `<div class="page-head"><div>${breadcrumb(p)}<h1>${title}</h1></div>
      <div class="toolbar" style="margin:0">
        <label class="muted">Group by <select id="groupby">${Object.keys(GROUPS).map(k => `<option value="${k}" ${ui.group === k ? 'selected' : ''}>${k === 'none' ? 'None' : GROUPS[k].l}</option>`).join('')}</select></label>
        <div class="dropdown" id="colmenu"><button class="btn" data-act="toggleCols">Columns ▾</button>
          <div class="menu">${COLS.map(c => `<label><input type="checkbox" data-col="${c.k}" ${ui.cols.has(c.k) ? 'checked' : ''}> ${c.l}</label>`).join('')}</div></div>
      </div></div>
      ${filterBar(p)}
      <div class="card flush"><div class="tablewrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table></div></div>`;
  }

  /* ---------- detail panel ---------- */
  function renderPanel() {
    const el = $('#panel'), bd = $('#backdrop');
    const t = ui.openId && byId(ui.openId);
    el.classList.toggle('open', !!t); bd.classList.toggle('open', !!t);
    if (!t) { el.innerHTML = ''; return; }
    const known = t.origCurrency !== 'unknown', foreign = known && t.origCurrency !== 'ILS';
    const curTrip = tripOf(t), tagged = curTrip && isManual(curTrip, t);
    const kv = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
    let fx;
    if (!known) fx = '<div class="callout"><span>Original currency is <b>unknown</b> – pending validation. Only the billed ₪ amount is available, so this row is not counted in per-currency totals or auto-included in trips.</span></div>';
    else if (!foreign) fx = '<span class="muted">Billed in ILS – no currency conversion.</span>';
    else {
      const dev = (t.effectiveRate / t.statedRate - 1) * 100;
      fx = `<dl class="kv">${kv('Original amount', fmtFx(t.origCurrency, t.origAmount))}${kv('Stated rate', '₪' + t.statedRate.toFixed(4) + ' / ' + SYM[t.origCurrency] + '1')}
        ${kv('Effective rate', '₪' + t.effectiveRate.toFixed(4) + ' / ' + SYM[t.origCurrency] + '1')}${kv('Commission', fmtILS(t.commission))}
        ${kv('Effective vs stated', (dev >= 0 ? '+' : '') + dev.toFixed(2) + '%')}${kv('Billed', fmtILS(t.amount))}</dl>`;
    }
    let inst = '';
    if (t.installment) {
      const i = t.installment, per = t.amount;
      inst = `<section><h3>Installment plan</h3><div><b>Payment ${i.n} of ${i.total}</b></div>
        <div style="background:var(--surface-2);border-radius:4px;height:8px;margin:8px 0;overflow:hidden"><div style="width:${i.n / i.total * 100}%;height:100%;background:var(--primary)"></div></div>
        <dl class="kv">${kv('Plan total', fmtILS(i.planTotal))}${kv('Per payment', fmtILS(per))}${kv('Remaining', (i.total - i.n) + ' payments · ' + fmtILS((i.total - i.n) * per))}</dl></section>`;
    }
    el.innerHTML = `<header><div><h2 dir="auto">${esc(t.merchant)}</h2><div class="muted">${fmtDate(t.txDate)} · <span dir="auto">${esc(t.city)}</span>${t.city ? ', ' : ''}${esc(countryName(t.country))}</div></div>
        <button class="close" data-act="closePanel" title="Close (Esc)">×</button></header>
      <section><div class="big-amount">${amt(t.amount)}</div><div class="row-inline">${typeBadge(t)} ${foreign ? '<span class="muted">' + fmtFx(t.origCurrency, t.origAmount) + '</span>' : ''}</div></section>
      <section><h3>Review</h3>${t.reviewReasons.length ? t.reviewReasons.map(r => `<div class="callout"><span>⚠ ${esc(REVIEW_TEXT[r] || r)}</span></div>`).join('') + `<a href="${REVIEW_URL}">Resolve in currency review →</a>` : '<span class="badge ok">No review reasons</span>'}</section>
      <section><h3>Details</h3><dl class="kv">${kv('Transaction date', fmtDate(t.txDate))}${kv('Charge date', fmtDate(t.chargeDate))}${kv('Card', esc(t.card))}${kv('Type', esc(t.type))}${kv('Notes', t.notes ? `<span dir="auto">${esc(t.notes)}</span>` : '<span class="muted">—</span>')}</dl></section>
      <section><h3>Category</h3><dl class="kv">${kv('Bank category (raw)', `<span dir="auto">${esc(t.bankCategory)}</span> <span class="muted">${esc(catEn(t.bankCategory))}</span>`)}
        ${kv('Custom category', t.customCategory ? `<span dir="auto">${esc(t.customCategory)}</span> <span class="badge info">override</span>` : '<span class="muted">none</span>')}
        ${kv('Counted as', `<b dir="auto">${esc(effCat(t))}</b>`)}</dl>
        <div class="row-inline" style="margin-top:10px"><select id="ovr" style="flex:1;min-width:160px"><option value="" disabled ${t.customCategory ? '' : 'selected'}>Override category…</option>
          ${customCats().map(c => `<option value="${esc(c)}" ${c === t.customCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}
          <option value="__new">Create new…</option>${t.customCategory ? '<option value="__clear">Clear override</option>' : ''}</select></div>
        <div class="muted" style="font-size:12px;margin-top:4px">The bank text is always kept; the override only changes how this transaction is counted.</div></section>
      <section><h3>Currency &amp; FX</h3>${fx}</section>${inst}
      <section><h3>Trip</h3><div class="row-inline">${curTrip ? `<span class="chip tag${tagged ? '' : ' static'}">${esc(curTrip.name)} <span class="muted">· ${tagged ? 'tagged' : 'auto'}</span>${tagged ? '<button data-act="untag" title="Remove tag">×</button>' : ''}</span>` : '<span class="muted">No trip</span>'}</div>
        <div class="row-inline" style="margin-top:10px"><select id="tagsel"><option value="" disabled selected>Set trip…</option>${TRIPS.filter(tr => !(tagged && tr === curTrip)).map(tr => `<option value="${tr.id}">${esc(tr.name)}</option>`).join('')}</select></div></section>`;
  }

  /* ---------- trips ---------- */
  function tripStats(tr) {
    const members = TX.filter(t => isMember(tr, t));
    const s = totals(members);
    s.members = members;
    s.manual = members.filter(t => isManual(tr, t));
    s.auto = members.filter(t => !isManual(tr, t));
    s.days = dayDiff(tr.start, tr.end) + 1;
    return s;
  }
  const tripRange = tr => fmtDate(tr.start) + ' – ' + fmtDate(tr.end);

  function renderTrips() {
    return `<div class="page-head"><div><div class="crumbs">Trips</div><h1>Trips</h1></div><a class="btn primary" href="#/trips/new" style="text-decoration:none">+ Create trip</a></div>
      <div class="grid cols-3">${TRIPS.map(tr => {
        const s = tripStats(tr);
        return `<div class="card trip-card" data-act="openTrip" data-trip="${tr.id}"><h2>${esc(tr.name)}</h2>
          <div class="muted">${tripRange(tr)} · ${s.days} days · <span class="badge">${tr.currency}</span></div>
          <div class="stat-row"><div class="stat"><div class="label">Billed</div><div class="value">${fmtILS(s.ils)}</div></div>
            ${tr.currency !== 'ILS' ? `<div class="stat"><div class="label">In ${tr.currency}</div><div class="value">${fmtFxCompact(tr.currency, s.foreign[tr.currency] || 0)}</div></div>` : ''}
            <div class="stat"><div class="label">Transactions</div><div class="value">${s.count}</div></div></div>
          <div class="muted" style="margin-top:10px">${s.manual.length} tagged · ${s.auto.length} auto-included</div></div>`;
      }).join('')}</div>`;
  }

  function miniTable(rows, actions, emptyMsg) {
    if (!rows.length) return `<div class="empty">${emptyMsg}</div>`;
    return `<div class="tablewrap"><table><thead><tr><th>Date</th><th>Merchant</th><th>Category</th><th class="num">Original</th><th class="num">Billing (₪)</th><th></th></tr></thead><tbody>${rows.map(t =>
      `<tr class="row" data-act="openTx" data-id="${t.id}"><td class="nowrap">${fmtDate(t.txDate)}</td><td dir="auto" class="text-cell">${esc(t.merchant)}</td><td dir="auto" class="nowrap">${esc(effCat(t))}</td>
        <td class="num">${origText(t)}</td><td class="num">${amt(t.amount)}</td><td class="num nowrap">${actions(t)}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderTripDetail(id) {
    const tr = tripById(id);
    if (!tr) return '<div class="card">Trip not found. <a href="#/trips">Back to trips</a></div>';
    const s = tripStats(tr);
    const inside = s.members.filter(t => inRange(tr, t)), outside = s.members.filter(t => !inRange(tr, t));
    const cat = {};
    s.members.forEach(t => { cat[effCat(t)] = (cat[effCat(t)] || 0) + t.amount; });
    const cats = Object.entries(cat).sort((a, b) => b[1] - a[1]), maxCat = Math.max(...cats.map(c => Math.abs(c[1])), 1);
    const days = [];
    for (let i = 0; i < s.days; i++) days.push(addDays(tr.start, i));
    const perDay = Object.fromEntries(days.map(d => [d, 0]));
    inside.forEach(t => { perDay[t.txDate] += t.amount; });
    const maxDay = Math.max(...Object.values(perDay), 1);
    const excluded = TX.filter(t => isExcluded(tr, t));
    const q = ui.tripSearch.trim().toLowerCase();
    const cand = TX.filter(t => !isMember(tr, t) && (q ? (t.merchant + ' ' + t.txDate + ' ' + t.bankCategory + ' ' + (t.customCategory || '')).toLowerCase().includes(q)
      : t.txDate >= addDays(tr.start, -30) && t.txDate <= addDays(tr.end, 7))).slice(0, 8);
    const foreignOthers = Object.keys(s.foreign).filter(c => c !== tr.currency);

    return `<div class="page-head"><div><div class="crumbs"><a href="#/trips">Trips</a><span class="sep">›</span><b>${esc(tr.name)}</b></div><h1>${esc(tr.name)}</h1>
        <div class="muted">${tripRange(tr)} · ${s.days} days · trip currency <span class="badge">${tr.currency}</span></div></div>
        <a class="btn" href="#/transactions?trip=${tr.id}" style="text-decoration:none">View all in Transactions →</a></div>
      <div class="card" style="margin-bottom:16px"><div class="hero">
        <div class="stat"><div class="label">Total billed (ILS)</div><div class="value" style="font-size:34px">${fmtILS(s.ils)}</div></div>
        ${tr.currency !== 'ILS' ? `<div class="stat"><div class="label">Spent in ${tr.currency}</div><div class="value" style="font-size:34px">${fmtFxCompact(tr.currency, s.foreign[tr.currency] || 0)}</div><div class="muted">${fmtFx(tr.currency, s.foreign[tr.currency] || 0)}</div></div>` : ''}
        ${foreignOthers.map(c => `<div class="stat"><div class="label">Also in ${c}</div><div class="value sm">${fmtFx(c, s.foreign[c])}</div></div>`).join('')}
        <div class="stat"><div class="label">Transactions</div><div class="value sm">${s.count}</div></div>
        <div class="stat"><div class="label">Avg / day</div><div class="value sm">${fmtILS(inside.reduce((a, t) => a + t.amount, 0) / s.days)}</div></div>
        <div class="stat"><div class="label">FX commission</div><div class="value sm">${fmtILS(s.commission)}</div></div></div>
        ${outside.length ? `<div class="muted" style="margin-top:10px">${outside.length} tagged transaction(s) fall outside the trip dates (${fmtILS(totals(outside).ils)}) – included in totals, not in the daily chart.</div>` : ''}
        ${s.unknown ? `<div class="muted" style="margin-top:6px">${s.unknown} included transaction(s) have unknown currency.</div>` : ''}</div>
      <div class="grid cols-2" style="margin-bottom:16px">
        <div class="card"><h2>By category (₪)</h2>${cats.map(([c, v]) => `<div class="hbar"><span class="lbl" dir="auto" title="${esc(catEn(c))}"><a href="#/transactions?trip=${tr.id}&category=${encodeURIComponent(c)}">${esc(c)}</a></span>
          <div class="track"><div class="fill" style="width:${Math.max(0, v) / maxCat * 100}%"></div></div><span class="num">${fmtILS(v)}</span></div>`).join('')}</div>
        <div class="card"><h2>By day (₪ billed, by transaction date)</h2><div class="daychart">${days.map(d => `<div class="col" title="${fmtDate(d)}: ${fmtILS(perDay[d])}"><div class="bar" style="height:${perDay[d] / maxDay * 100}%"></div></div>`).join('')}</div>
          <div class="dayaxis">${days.map((d, i) => `<span>${i % 3 === 0 ? d.slice(8) : ''}</span>`).join('')}</div>
          <div class="muted" style="font-size:12px;margin-top:6px">Peaks are mostly hotel charges posted at check-out.</div></div></div>
      <div class="card flush" style="margin-bottom:16px"><div style="padding:14px 16px 0"><h2>Manually tagged <span class="muted">(${s.manual.length})</span></h2></div>
        ${miniTable(s.manual, t => `<button class="btn sm" data-act="tripUntag" data-trip="${tr.id}" data-id="${t.id}">Remove tag</button>`, 'Nothing tagged manually yet.')}</div>
      <div class="card flush" style="margin-bottom:16px"><div style="padding:14px 16px 0"><h2>Auto-included <span class="muted">(${s.auto.length}) · currency ${tr.currency} within the trip dates</span></h2></div>
        ${miniTable(s.auto, t => `<button class="btn sm" data-act="tripTag" data-trip="${tr.id}" data-id="${t.id}" title="Tag so it stays in this trip">Tag</button> <button class="btn sm danger" data-act="tripExclude" data-trip="${tr.id}" data-id="${t.id}">Exclude</button>`, 'No transactions matched automatically.')}</div>
      ${excluded.length ? `<div class="card flush" style="margin-bottom:16px"><div style="padding:14px 16px 0"><h2>Excluded from auto-inclusion <span class="muted">(${excluded.length})</span></h2></div>
        ${miniTable(excluded, t => `<button class="btn sm" data-act="tripRestore" data-trip="${tr.id}" data-id="${t.id}">Restore</button>`, '')}</div>` : ''}
      <div class="card"><h2>Add transactions to this trip</h2>
        <div class="row-inline"><input id="tripq" placeholder="Search merchant, date (2025-12-31) or category…" value="${esc(ui.tripSearch)}" style="min-width:300px"><button class="btn" data-act="tripSearch">Search</button></div>
        <div class="muted" style="margin-top:6px;font-size:12px">${q ? 'Results' : 'Suggestions: not yet in this trip, from 30 days before to 7 days after'}</div>
        <div class="search-results">${cand.length ? cand.map(t => `<div class="item"><span><span class="muted nowrap">${fmtDate(t.txDate)}</span> · <span dir="auto">${esc(t.merchant)}</span> · ${origText(t)} · ${amt(t.amount)}</span>
          <button class="btn sm primary" data-act="tripTag" data-trip="${tr.id}" data-id="${t.id}">+ Tag</button></div>`).join('') : '<div class="muted">No results.</div>'}</div></div>`;
  }

  function renderNewTrip(err) {
    return `<div class="page-head"><div><div class="crumbs"><a href="#/trips">Trips</a><span class="sep">›</span><b>New trip</b></div><h1>Create trip</h1></div></div>
      <div class="card" style="max-width:760px">${err ? `<div class="err">${esc(err)}</div>` : ''}
        <div class="form-grid"><label class="field">Name<input id="nt-name" placeholder="e.g. Summer in Greece" style="width:100%"></label>
          <label class="field">Currency<select id="nt-cur">${CURRENCIES.map(c => `<option>${c}</option>`).join('')}</select></label>
          <label class="field">Start date<input id="nt-start" type="date"></label><label class="field">End date<input id="nt-end" type="date"></label></div>
        <p class="muted">Transactions in the trip currency within the date range are included automatically. You can also tag individual transactions (any currency) from the trip page or from a transaction's detail panel.</p>
        <div class="row-inline"><button class="btn primary" data-act="createTrip">Create trip</button><a class="btn" href="#/trips" style="text-decoration:none">Cancel</a></div></div>`;
  }

  function renderAnalysis() {
    const L = (h, t) => `<li><a href="#/transactions?${h}">${t}</a> <span class="muted">#/transactions?${h}</span></li>`;
    return `<div class="page-head"><div><div class="crumbs">Analysis</div><h1>Analysis</h1></div></div>
      <div class="card"><p>Placeholder – the charts live in another prototype. Every chart drills down into the Transactions list via a hash URL. Try a few:</p><ul>
        ${L('category=Food&month=2026-01', 'Food, Jan 2026')}${L('category=' + encodeURIComponent('מסעדות קפה וברים') + '&month=2026-02', 'Dining, Feb 2026 (Hebrew category value)')}
        ${L('trip=japan-winter', 'Winter trip in Japan')}${L('currency=USD', 'All USD transactions')}${L('review=1', 'Needs review (unknown currency)')}${L('type=INSTALLMENTS', 'Installments')}</ul></div>`;
  }

  /* ---------- render ---------- */
  function render() {
    const { path, params } = route();
    const view = $('#view');
    let nav = 'transactions';
    if (path.startsWith('/trips')) {
      nav = 'trips';
      const id = path.split('/')[2];
      view.innerHTML = id === 'new' ? renderNewTrip() : id ? renderTripDetail(id) : renderTrips();
    } else if (path.startsWith('/analysis')) { nav = 'analysis'; view.innerHTML = renderAnalysis(); }
    else view.innerHTML = renderTransactions(params);
    document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === nav));
    renderPanel();
  }
  const refresh = () => render();

  /* ---------- events ---------- */
  document.addEventListener('click', e => {
    const open = $('#colmenu');
    if (open && open.classList.contains('open') && !e.target.closest('#colmenu')) open.classList.remove('open');
    const a = e.target.closest('[data-act]');
    if (!a) return;
    const d = a.dataset, t = d.id && byId(d.id), tr = d.trip && tripById(d.trip);
    switch (d.act) {
      case 'openTx': ui.openId = d.id; render(); break;
      case 'closePanel': ui.openId = null; render(); break;
      case 'sort':
        ui.sort = ui.sort.key === d.key ? { key: d.key, dir: -ui.sort.dir } : { key: d.key, dir: 1 }; render(); break;
      case 'toggleGroup': ui.collapsed.has(d.key) ? ui.collapsed.delete(d.key) : ui.collapsed.add(d.key); render(); break;
      case 'toggleCols': $('#colmenu').classList.toggle('open'); break;
      case 'rmFilter': setTxParams({}, d.keys.split(',')); break;
      case 'clearFilters': go('#/transactions'); break;
      case 'addFilter': {
        const def = FDEFS.find(x => x.id === ui.draftField), set = {};
        def.inputs.forEach(i => {
          if (i.t === 'fixed') set[i.k] = i.value;
          else { const v = document.querySelector(`[data-fin="${i.k}"]`).value.trim(); if (v !== '') set[i.k] = v; }
        });
        if (Object.keys(set).length) setTxParams(set);
        break;
      }
      case 'untag': byId(ui.openId).tripTag = null; refresh(); break;
      case 'openTrip': go('#/trips/' + d.trip); break;
      case 'tripTag': tagTrip(t, tr); refresh(); break;
      case 'tripUntag': t.tripTag = null; refresh(); break;
      case 'tripExclude': if (!tr.excluded.includes(t.id)) tr.excluded.push(t.id); refresh(); break;
      case 'tripRestore': tr.excluded = tr.excluded.filter(i => i !== t.id); refresh(); break;
      case 'tripSearch': ui.tripSearch = $('#tripq').value; refresh(); break;
      case 'createTrip': {
        const name = $('#nt-name').value.trim(), cur = $('#nt-cur').value, s = $('#nt-start').value, en = $('#nt-end').value;
        const err = !name ? 'Please enter a name.' : !s || !en ? 'Please choose start and end dates.' : en < s ? 'End date must not be before the start date.' : '';
        if (err) { $('#view').innerHTML = renderNewTrip(err); $('#nt-name').value = name; $('#nt-cur').value = cur; $('#nt-start').value = s; $('#nt-end').value = en; break; }
        const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') + '-' + (TRIPS.length + 1);
        TRIPS.push({ id, name, currency: cur, start: s, end: en, excluded: [] });
        go('#/trips/' + id);
        break;
      }
    }
  });
  document.addEventListener('change', e => {
    const x = e.target;
    if (x.id === 'ffield') { ui.draftField = x.value; render(); }
    else if (x.id === 'groupby') { ui.group = x.value; render(); }
    else if (x.id === 'ovr') { setCustomCategory(byId(ui.openId), x.value); render(); }
    else if (x.id === 'tagsel') { tagTrip(byId(ui.openId), tripById(x.value)); render(); }
    else if (x.dataset && x.dataset.col) { x.checked ? ui.cols.add(x.dataset.col) : ui.cols.delete(x.dataset.col); const o = $('#colmenu').classList.contains('open'); render(); if (o) $('#colmenu').classList.add('open'); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && ui.openId) { ui.openId = null; render(); }
    if (e.key === 'Enter' && e.target.id === 'tripq') { ui.tripSearch = e.target.value; refresh(); }
    if (e.key === 'Enter' && e.target.dataset && e.target.dataset.fin) document.querySelector('[data-act="addFilter"]').click();
  });
  window.addEventListener('hashchange', render);
  render();
})();

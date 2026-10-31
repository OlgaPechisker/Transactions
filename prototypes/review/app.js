(function () {
  "use strict";
  const KEY = "txn-proto-review-v1";
  const PAGE = 40;
  const CURS = ["JPY", "USD", "EUR", "GBP"];
  const COUNTRY_NAMES = { JP: "Japan", US: "United States", NL: "Netherlands", FR: "France", DE: "Germany", GB: "United Kingdom", LU: "Luxembourg", TH: "Thailand", SG: "Singapore" };
  const countryName = (c) => COUNTRY_NAMES[c] || c;
  const M = window.MOCK;
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n, d = 2) => Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

  // ---------- state (separate decision layer; bank rows in MOCK are never mutated) ----------
  const defaults = () => ({
    validated: {},
    customCats: M.defaultCustomCats.slice(),
    mapping: { "מזון וצריכה": "Groceries", "מסעדות קפה וברים": "Dining", "טיסות ותיירות": "Travel", "פנאי בידור וספורט": "Entertainment" },
    rules: [{ id: "r1", terms: ["PAZ", "SONOL", "DELEK", "פז", "סונול", "דלק מנטה"], cat: "Petrol" }],
    overrides: {}
  });
  let S;
  try { S = Object.assign(defaults(), JSON.parse(localStorage.getItem(KEY) || "{}")); } catch (e) { S = defaults(); }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* file:// storage may be blocked */ } };

  // ---------- currency inference ----------
  const RANGES = [
    { cur: "JPY", lo: 0.01, hi: 0.05, hint: "rate 0.01–0.05 ILS per unit" },
    { cur: "USD", lo: 3.0, hi: 3.75, hint: "rate 3.0–3.75" },
    { cur: "USD", lo: 3.75, hi: 3.9, hint: "between USD and EUR", low: true },
    { cur: "EUR", lo: 3.9, hi: 4.3, hint: "rate 3.9–4.3" },
    { cur: "GBP", lo: 4.4, hi: 5.0, hint: "rate 4.4–5.0" }
  ];
  function suggest(r) {
    for (const g of RANGES) if (r.rate >= g.lo && r.rate < g.hi) {
      let conf = g.low ? "low" : "high";
      if (g.cur === "JPY" && (r.rate < 0.018 || r.rate > 0.023)) conf = "medium";
      return { cur: g.cur, conf, hint: g.hint };
    }
    return { cur: null, conf: "low", hint: "rate matches no listed currency" };
  }
  M.pending.forEach((r) => (r.sug = suggest(r)));
  const confText = { high: "high confidence", medium: "medium confidence", low: "low – check manually" };

  // ---------- category resolution ----------
  const termsMatch = (rule, t) => { const m = t.merchant.toLowerCase(); return rule.terms.some((x) => m.includes(x.toLowerCase())); };
  function resolve(t) {
    if (S.overrides[t.id]) return { cat: S.overrides[t.id], src: "override" };
    const rule = S.rules.find((r) => termsMatch(r, t));
    if (rule) return { cat: rule.cat, src: "rule", rule };
    if (S.mapping[t.bankCat]) return { cat: S.mapping[t.bankCat], src: "mapping" };
    return { cat: t.bankCat, src: "bank" };
  }
  const baseCat = (t) => S.mapping[t.bankCat] || t.bankCat;

  // ---------- toast / undo ----------
  let toastTimer, undoFn;
  function toast(msg, undo) {
    clearTimeout(toastTimer);
    undoFn = undo;
    $("#toast-host").innerHTML = `<div class="toast"><span>${esc(msg)}</span>${undo ? '<button data-act="undo">Undo</button>' : ""}</div>`;
    toastTimer = setTimeout(() => ($("#toast-host").innerHTML = ""), 8000);
  }
  function applyValidation(ids, cur, label) {
    const prev = {};
    ids.forEach((i) => { prev[i] = S.validated[i]; S.validated[i] = cur; ui.sel.delete(i); });
    save();
    toast(`${label}: ${ids.length} row${ids.length === 1 ? "" : "s"} validated as ${cur}`, () => {
      ids.forEach((i) => { if (prev[i] === undefined) delete S.validated[i]; else S.validated[i] = prev[i]; });
      save(); render();
    });
    render();
  }

  // ---------- review page ----------
  const ui = { tab: "pending", page: 0, sel: new Set(), f: { country: "", from: "", to: "", cur: "", q: "", conf: "" }, catSel: null, ovQ: "", ovPage: 0, ovOnly: "" };
  const pendingRows = () => M.pending.filter((r) => !S.validated[r.id]);
  const filtered = () => pendingRows().filter((r) => {
    const f = ui.f;
    return (!f.country || r.country === f.country) && (!f.from || r.date >= f.from) && (!f.to || r.date <= f.to) &&
      (!f.cur || r.sug.cur === f.cur) && (!f.conf || r.sug.conf === f.conf) && (!f.q || r.merchant.toLowerCase().includes(f.q.toLowerCase()));
  });
  const curSelect = (cls, act, id, selected) =>
    `<select class="${cls}" data-act="${act}" data-id="${id}"><option value="">Pick currency…</option>${CURS.map((c) => `<option ${c === selected ? "selected" : ""}>${c}</option>`).join("")}</select>`;

  function reviewPage() {
    const pend = pendingRows(), done = M.TOTAL - pend.length, pct = Math.round((done / M.TOTAL) * 100);
    const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / PAGE));
    if (ui.page >= pages) ui.page = pages - 1;
    const slice = rows.slice(ui.page * PAGE, ui.page * PAGE + PAGE);
    const countries = [...new Set(M.pending.map((r) => r.country))].sort();

    // group suggestions (only confident groups can be bulk-confirmed)
    const groups = {};
    pend.forEach((r) => { if (r.sug.conf !== "low") (groups[r.sug.cur] = groups[r.sug.cur] || []).push(r); });
    const lowCount = pend.filter((r) => r.sug.conf === "low").length;
    const groupHtml = Object.keys(groups).map((cur) => {
      const g = groups[cur], rates = g.map((r) => r.rate), lo = Math.min(...rates), hi = Math.max(...rates);
      const txt = cur === "JPY" ? `These <b>${g.length}</b> rows have a rate between <b>0.01 and 0.05</b> → <b>JPY</b>`
        : `These <b>${g.length}</b> rows have a rate between <b>${lo.toFixed(2)} and ${hi.toFixed(2)}</b> → <b>${cur}</b>`;
      return `<div class="suggest"><div class="txt">${txt}<div class="note">Inferred from the statement's stated FX rate; the bank's empty currency cell stays empty.</div></div>
        <button class="primary" data-act="confirm-group" data-cur="${cur}">Confirm all ${g.length} as ${cur}</button></div>`;
    }).join("") + (lowCount ? `<div class="suggest warn"><div class="txt"><b>${lowCount}</b> rows have an ambiguous or unknown rate – no group action offered, please decide per row.</div>
        <button data-act="filter-low">Show them</button></div>` : "");

    const allOnPage = slice.length && slice.every((r) => ui.sel.has(r.id));
    const body = ui.tab === "pending" ? `
      ${ui.sel.size ? `<div class="bulkbar"><b>${ui.sel.size} selected</b>
        <button class="primary" data-act="bulk-apply" data-cur="JPY">Apply JPY to selected</button>
        <select data-act="bulk-pick"><option value="">Apply other currency…</option>${CURS.filter((c) => c !== "JPY").map((c) => `<option>${c}</option>`).join("")}</select>
        <button data-act="bulk-confirm">Confirm each suggestion</button>
        <span class="spacer"></span><button data-act="clear-sel">Clear selection</button></div>` : ""}
      <div class="table-scroll"><table><thead><tr>
        <th><input type="checkbox" data-act="sel-page" ${allOnPage ? "checked" : ""}></th>
        <th>Date</th><th>Merchant</th><th>Country</th><th class="num">Original amt</th><th class="num">Billing ILS</th><th class="num">Stated rate</th><th>Suggested currency</th><th>Decision</th></tr></thead><tbody>
        ${slice.map((r) => `<tr class="${ui.sel.has(r.id) ? "sel" : ""}">
          <td><input type="checkbox" data-act="sel" data-id="${r.id}" ${ui.sel.has(r.id) ? "checked" : ""}></td>
          <td>${r.date}</td><td class="merchant" title="${esc(r.merchant)}">${esc(r.merchant)}</td><td title="${r.country}">${esc(countryName(r.country))}</td>
          <td class="num">${fmt(r.orig)}</td><td class="num">₪${fmt(r.ils)}</td><td class="num">${r.rate.toFixed(4)}</td>
          <td class="wrap">${r.sug.cur ? `<span class="cur">${r.sug.cur}</span> <span class="badge inferred" title="Inferred from stated rate, not from the bank">inferred</span>` : `<span class="note">No suggestion</span>`}
            <div class="conf ${r.sug.conf}">${confText[r.sug.conf]} · ${r.sug.hint}</div></td>
          <td><div class="row" style="flex-wrap:nowrap">${r.sug.cur ? `<button class="sm primary" data-act="confirm" data-id="${r.id}">Confirm ${r.sug.cur}</button>` : ""}${curSelect("sm", "pick", r.id, "")}</div></td></tr>`).join("")}
      </tbody></table></div>
      ${rows.length ? "" : `<div class="empty">${pend.length ? "No rows match the filters." : "<b>All done 🎉</b>Every foreign-currency row has been validated."}</div>`}
      <div class="pager"><span>${rows.length} rows${rows.length !== pend.length ? ` (filtered from ${pend.length})` : ""}</span>
        <button class="sm" data-act="page" data-d="-1" ${ui.page === 0 ? "disabled" : ""}>‹ Prev</button><span>Page ${ui.page + 1} / ${pages}</span>
        <button class="sm" data-act="page" data-d="1" ${ui.page >= pages - 1 ? "disabled" : ""}>Next ›</button>
        ${rows.length > slice.length ? `<button class="sm" data-act="sel-all-filtered">Select all ${rows.length} filtered</button>` : ""}</div>`
      : validatedTable();

    return `<h1>Review queue – currency validation</h1>
      <p class="sub">Statement rows where the original-currency cell is empty. Your decision is saved separately; the bank's data is never overwritten.</p>
      <div class="card"><div class="progress-wrap"><div class="counter">${done} <small>of ${M.TOTAL} validated</small></div>
        <div class="bar"><i style="width:${pct}%"></i></div><div class="pct">${pct}%</div></div></div>
      ${ui.tab === "pending" ? groupHtml : ""}
      <div class="card" style="margin-top:16px">
        <div class="tabs"><button class="${ui.tab === "pending" ? "on" : ""}" data-act="tab" data-t="pending">Pending (${pend.length})</button>
          <button class="${ui.tab === "done" ? "on" : ""}" data-act="tab" data-t="done">Validated (${done})</button></div>
        ${ui.tab === "pending" ? `<div class="row" style="margin-bottom:12px">
          <label class="f">Merchant country<select data-f="country"><option value="">All</option>${countries.map((c) => `<option value="${c}" ${ui.f.country === c ? "selected" : ""}>${esc(countryName(c))} (${c})</option>`).join("")}</select></label>
          <label class="f">From<input type="date" data-f="from" value="${ui.f.from}"></label>
          <label class="f">To<input type="date" data-f="to" value="${ui.f.to}"></label>
          <label class="f">Suggested currency<select data-f="cur"><option value="">All</option>${CURS.map((c) => `<option ${ui.f.cur === c ? "selected" : ""}>${c}</option>`).join("")}</select></label>
          <label class="f">Merchant contains<input type="search" data-f="q" value="${esc(ui.f.q)}" placeholder="press Enter"></label>
          ${ui.f.conf ? `<span class="badge inferred" style="align-self:flex-end">Low confidence only</span>` : ""}
          <button class="ghost" data-act="clear-f" style="align-self:flex-end">Clear filters</button></div>` : ""}
        ${body}</div>`;
  }
  function validatedTable() {
    const rows = M.pending.filter((r) => S.validated[r.id]);
    if (!rows.length) return `<div class="empty">Nothing validated yet.</div>`;
    return `<div class="table-scroll"><table><thead><tr><th>Date</th><th>Merchant</th><th class="num">Original amt</th><th class="num">Billing ILS</th><th>Validated currency</th><th>Bank original cell</th><th></th></tr></thead><tbody>
      ${rows.slice(0, 100).map((r) => `<tr><td>${r.date}</td><td class="merchant">${esc(r.merchant)}</td><td class="num">${fmt(r.orig)}</td><td class="num">₪${fmt(r.ils)}</td>
      <td><span class="badge ok">${S.validated[r.id]}</span></td><td class="note">(empty) – unchanged</td>
      <td><button class="sm" data-act="revert" data-id="${r.id}">Move back to queue</button></td></tr>`).join("")}</tbody></table></div>
      ${rows.length > 100 ? `<div class="note" style="margin-top:8px">Showing first 100 of ${rows.length}.</div>` : ""}`;
  }

  // ---------- categories page ----------
  const allCats = () => [...new Set([...S.customCats, ...Object.values(S.mapping), ...S.rules.map((r) => r.cat)])].sort();
  function catOptions(sel, blank) {
    return `<option value="">${blank}</option>${allCats().map((c) => `<option ${c === sel ? "selected" : ""}>${esc(c)}</option>`).join("")}<option value="__new">＋ Create new…</option>`;
  }
  function categoriesPage() {
    const counts = {};
    M.txns.forEach((t) => (counts[t.bankCat] = (counts[t.bankCat] || 0) + 1));
    const bank = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
    const result = {};
    M.txns.forEach((t) => { const c = resolve(t).cat; result[c] = (result[c] || 0) + 1; });

    const ruleHtml = S.rules.map((r) => {
      const matches = M.txns.filter((t) => termsMatch(r, t));
      const applied = matches.filter((t) => resolve(t).rule === r);
      const moved = applied.filter((t) => baseCat(t) !== r.cat).length;
      const manual = matches.filter((t) => S.overrides[t.id]).length;
      const shadow = matches.length - applied.length - manual;
      return `<div class="rule-row"><span>merchant contains <code>${r.terms.map(esc).join("</code> / <code>")}</code> →</span> <span class="badge rule">${esc(r.cat)}</span>
        <span class="pv"><b>${matches.length}</b> match · changes <b>${moved}</b> transaction${moved === 1 ? "" : "s"}${manual ? ` · ${manual} kept by manual override` : ""}${shadow > 0 ? ` · ${shadow} taken by an earlier rule` : ""}</span>
        <button class="sm danger" data-act="del-rule" data-id="${r.id}">Delete</button></div>`;
    }).join("") || `<div class="note">No rules yet.</div>`;

    let list = M.txns.filter((t) => (!ui.catSel || t.bankCat === ui.catSel) && (!ui.ovQ || t.merchant.toLowerCase().includes(ui.ovQ.toLowerCase())));
    if (ui.ovOnly) list = list.filter((t) => resolve(t).src === ui.ovOnly);
    const pages = Math.max(1, Math.ceil(list.length / 25));
    if (ui.ovPage >= pages) ui.ovPage = pages - 1;
    const slice = list.slice(ui.ovPage * 25, ui.ovPage * 25 + 25);
    const srcBadge = { override: '<span class="badge ovr">manual override</span>', rule: '<span class="badge rule">rule</span>', mapping: '<span class="badge map">mapping</span>', bank: '<span class="badge bank">bank category</span>' };

    return `<h1>Category mapping</h1><p class="sub">Map the bank's categories to your own, add merchant rules, and override single transactions.</p>
      <div class="lock">🔒 <div><b>The bank's category text is never changed.</b> Your mappings, rules and overrides live in a separate layer; the original is always shown alongside.</div></div>
      <div class="grid2"><div>
        <div class="card"><h2>Bank categories → your category</h2>
          <ul class="catlist">${bank.map((b) => `<li class="${ui.catSel === b ? "active" : ""}">
            <span class="bn heb" data-act="focus-cat" data-cat="${esc(b)}" style="cursor:pointer" title="Filter transactions below">${esc(b)}</span><span class="cnt">${counts[b]} txns</span>
            <div class="arrow">→ <select class="sm" data-map="${esc(b)}">${catOptions(S.mapping[b], "(keep bank category)")}</select>
            ${S.mapping[b] ? `<button class="sm ghost" data-act="unmap" data-cat="${esc(b)}">reset</button>` : ""}</div></li>`).join("")}</ul>
          ${ui.catSel ? `<button class="sm" data-act="focus-cat" data-cat="">Show all categories</button>` : ""}</div>
        <div class="card"><h2>Result: your categories</h2>${Object.entries(result).sort((a, b) => b[1] - a[1]).map(([c, n]) =>
          `<div class="row" style="justify-content:space-between;padding:3px 0"><span class="${/[\u0590-\u05ff]/.test(c) ? "heb" : ""}">${esc(c)}</span><span class="note">${n}</span></div>`).join("")}</div>
      </div><div>
        <div class="card"><h2>Rules by merchant text</h2>${ruleHtml}
          <div class="row" style="margin-top:12px"><label class="f" style="flex:1;min-width:200px">Merchant contains (separate with , or /)<input type="text" id="rule-terms" placeholder="PAZ / SONOL / DELEK"></label>
            <label class="f">→ Category<select id="rule-cat">${catOptions("Petrol", "Choose…")}</select></label>
            <button class="primary" data-act="add-rule" style="align-self:flex-end">Add rule</button></div>
          <div class="note" style="margin-top:6px">First matching rule wins. Precedence: manual override › rule › bank-category mapping › bank category.</div></div>
        <div class="card"><h2>Transactions – original bank category vs. your category</h2>
          <div class="row" style="margin-bottom:10px"><input type="search" id="ov-q" placeholder="Search merchant (Enter)" style="min-width:210px" value="${esc(ui.ovQ)}">
            <select data-ovonly><option value="">All sources</option>${["override", "rule", "mapping", "bank"].map((s) => `<option value="${s}" ${ui.ovOnly === s ? "selected" : ""}>${s}</option>`).join("")}</select>
            <span class="note">${list.length} transactions${ui.catSel ? " in " + esc(ui.catSel) : ""}</span></div>
          <div class="table-scroll"><table><thead><tr><th>Date</th><th>Merchant</th><th class="num">ILS</th><th>🔒 Original bank category</th><th>Your category</th><th>Override</th></tr></thead><tbody>
          ${slice.map((t) => { const r = resolve(t); return `<tr><td>${t.date}</td><td class="merchant" title="${esc(t.merchant)}">${esc(t.merchant)}</td><td class="num">₪${fmt(t.ils)}</td>
            <td class="heb lockico">${esc(t.bankCat)}</td><td><b>${esc(r.cat)}</b> ${srcBadge[r.src]}</td>
            <td><select class="sm" data-ov="${t.id}">${catOptions(S.overrides[t.id], "—")}</select>${S.overrides[t.id] ? `<button class="sm ghost" data-act="clear-ov" data-id="${t.id}">clear</button>` : ""}</td></tr>`; }).join("")}
          </tbody></table></div>
          <div class="pager"><button class="sm" data-act="ovpage" data-d="-1" ${ui.ovPage === 0 ? "disabled" : ""}>‹ Prev</button><span>Page ${ui.ovPage + 1} / ${pages}</span>
            <button class="sm" data-act="ovpage" data-d="1" ${ui.ovPage >= pages - 1 ? "disabled" : ""}>Next ›</button></div></div>
      </div></div>`;
  }

  // ---------- rendering / routing ----------
  function render() {
    const route = location.hash.startsWith("#/categories") ? "categories" : "review";
    $("#nav-review").classList.toggle("active", route === "review");
    $("#nav-categories").classList.toggle("active", route === "categories");
    $("#nav-count").textContent = M.TOTAL - Object.keys(S.validated).length;
    $("#nav-count").style.display = M.TOTAL - Object.keys(S.validated).length ? "" : "none";
    const y = window.scrollY;
    $("#app").innerHTML = route === "review" ? reviewPage() : categoriesPage();
    window.scrollTo(0, y);
  }
  window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
  if (!location.hash) location.hash = "#/review";

  function newCategory() {
    const n = (prompt("New custom category name:") || "").trim();
    if (!n) return null;
    if (!allCats().includes(n)) S.customCats.push(n);
    return n;
  }

  document.addEventListener("click", (e) => {
    if (e.target.id === "reset-demo") {
      if (confirm("Clear all saved decisions (validations, mappings, rules, overrides) and restore the demo defaults?")) { S = defaults(); ui.sel.clear(); save(); toast("Demo data reset"); render(); }
      return;
    }
    const el = e.target.closest("[data-act]");
    if (!el || el.tagName === "SELECT") return;
    const a = el.dataset.act, id = el.dataset.id, byId = (i) => M.pending.find((r) => r.id === i);
    switch (a) {
      case "undo": if (undoFn) { undoFn(); undoFn = null; toast("Undone"); } break;
      case "tab": ui.tab = el.dataset.t; ui.page = 0; render(); break;
      case "page": ui.page += +el.dataset.d; render(); break;
      case "confirm": applyValidation([id], byId(id).sug.cur, "Confirmed suggestion"); break;
      case "confirm-group": { const cur = el.dataset.cur; applyValidation(pendingRows().filter((r) => r.sug.cur === cur && r.sug.conf !== "low").map((r) => r.id), cur, "Group confirmed"); break; }
      case "filter-low": ui.f = { country: "", from: "", to: "", cur: "", q: "", conf: "low" }; ui.page = 0; render(); break;
      case "bulk-apply": applyValidation([...ui.sel], el.dataset.cur, "Bulk apply"); break;
      case "bulk-confirm": {
        const ids = [...ui.sel].filter((i) => byId(i).sug.cur);
        if (!ids.length) { toast("None of the selected rows has a suggestion"); break; }
        ids.forEach((i) => { S.validated[i] = byId(i).sug.cur; ui.sel.delete(i); });
        save();
        toast(`Confirmed suggestions for ${ids.length} rows`, () => { ids.forEach((i) => delete S.validated[i]); save(); render(); });
        render(); break;
      }
      case "clear-sel": ui.sel.clear(); render(); break;
      case "sel": el.checked ? ui.sel.add(id) : ui.sel.delete(id); render(); break;
      case "sel-page": filtered().slice(ui.page * PAGE, ui.page * PAGE + PAGE).forEach((r) => (el.checked ? ui.sel.add(r.id) : ui.sel.delete(r.id))); render(); break;
      case "sel-all-filtered": filtered().forEach((r) => ui.sel.add(r.id)); render(); break;
      case "clear-f": ui.f = { country: "", from: "", to: "", cur: "", q: "", conf: "" }; ui.page = 0; render(); break;
      case "revert": { const c = S.validated[id]; delete S.validated[id]; save(); toast("Row moved back to the queue", () => { S.validated[id] = c; save(); render(); }); render(); break; }
      case "focus-cat": ui.catSel = el.dataset.cat || null; ui.ovPage = 0; render(); break;
      case "unmap": delete S.mapping[el.dataset.cat]; save(); render(); break;
      case "add-rule": {
        const terms = $("#rule-terms").value.split(/[,/|]/).map((s) => s.trim()).filter(Boolean);
        let cat = $("#rule-cat").value;
        if (!terms.length || !cat || cat === "__new") { toast("Enter merchant text and choose a category"); break; }
        S.rules.push({ id: "r" + Date.now(), terms, cat }); save(); toast("Rule added"); render(); break;
      }
      case "del-rule": { const i = S.rules.findIndex((r) => r.id === id), r = S.rules[i]; S.rules.splice(i, 1); save(); toast("Rule deleted", () => { S.rules.splice(i, 0, r); save(); render(); }); render(); break; }
      case "clear-ov": delete S.overrides[id]; save(); render(); break;
      case "ovpage": ui.ovPage += +el.dataset.d; render(); break;
    }
  });

  document.addEventListener("change", (e) => {
    const t = e.target, d = t.dataset;
    if (d.f) { ui.f[d.f] = t.value; ui.page = 0; render(); }
    else if (d.act === "pick" && t.value) applyValidation([d.id], t.value, "Picked currency");
    else if (d.act === "bulk-pick" && t.value) applyValidation([...ui.sel], t.value, "Bulk apply");
    else if (d.map !== undefined) {
      let v = t.value; if (v === "__new") v = newCategory();
      if (v) S.mapping[d.map] = v; else delete S.mapping[d.map];
      save(); render();
    } else if (d.ov) {
      let v = t.value; if (v === "__new") v = newCategory();
      if (v) S.overrides[d.ov] = v; else delete S.overrides[d.ov];
      save(); render();
    } else if (t.id === "ov-q") { ui.ovQ = t.value; ui.ovPage = 0; render(); }
    else if (d.ovonly !== undefined) { ui.ovOnly = t.value; ui.ovPage = 0; render(); }
    else if (t.id === "rule-cat" && t.value === "__new") {
      const terms = $("#rule-terms").value, n = newCategory(); render();
      $("#rule-terms").value = terms; if (n) $("#rule-cat").value = n;
    }
  });

  render();
})();

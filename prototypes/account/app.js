(function () {
  var DB = window.DB;
  var $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };

  var S = {
    user: sessionStorage.getItem('proto.user'),
    sim: 'clean',
    step: 1, file: null, result: null, identity: { mode: 'existing', cardId: '', label: '' }, imported: null
  };
  var MAX_FILE_BYTES = 10 * 1024 * 1024;

  var me = function () { return DB.users[S.user]; };
  var cardName = function (c) { return c.issuer + ' ••••' + c.last4 + (c.nickname ? ' · ' + c.nickname : ''); };
  var myCards = function () { return DB.cards.filter(function (c) { return c.owner === S.user; }); };
  function hashOf(name) { // fake short SHA-256 for display only
    var h = 5381; for (var i = 0; i < name.length; i++) h = ((h << 5) + h + name.charCodeAt(i)) >>> 0;
    return ('00000000' + h.toString(16)).slice(-8);
  }
  function toast(m) { var t = $('#toast'); t.textContent = m; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(function () { t.hidden = true; }, 2500); }
  function badge(s) {
    var cls = { SUCCESS: 'b-success', PARTIAL: 'b-warn', INFO: 'b-primary', WARNING: 'b-warn', ERROR: 'b-danger' }[s] || '';
    return '<span class="badge ' + cls + '">' + esc(s) + '</span>';
  }

  // ---------- chrome ----------
  function chrome(route) {
    if (!S.user) { $('#topbar').innerHTML = '<span class="brand">Expenses</span>'; return; }
    var links = [['import', 'Import'], ['cards', 'Profile & cards']];
    $('#topbar').innerHTML = '<span class="brand">Expenses</span><nav>' + links.map(function (l) {
      return '<a href="#/' + l[0] + '" class="' + (route === l[0] ? 'active' : '') + '">' + l[1] + '</a>';
    }).join('') + '</nav><div class="usermenu"><span class="muted">Signed in as</span><div class="avatar">' + esc(me().name[0]) + '</div>' +
      '<select data-act="switchUser" title="Simulated user switch">' + Object.keys(DB.users).map(function (k) {
        return '<option value="' + k + '"' + (k === S.user ? ' selected' : '') + '>' + DB.users[k].name + ' (' + DB.users[k].email + ')</option>';
      }).join('') + '</select><button data-act="logout">Log out</button></div>';
  }

  // ---------- pages ----------
  var pages = {};

  pages.login = function () {
    return '<div class="card login"><h1>Sign in</h1><p class="muted">Each owner sees only their own data.</p>' +
      '<label class="f">Email<input type="email" id="lemail" value="dan@example.com"></label>' +
      '<label class="f">Password<input type="password" value="••••••••"></label>' +
      '<button class="primary" style="width:100%" data-act="login">Sign in</button>' +
      '<p class="muted" style="margin-top:14px">Prototype: pick a mock account<br>' +
      '<a href="#" data-act="quick" data-u="dan">Dan (dan@example.com)</a> · <a href="#" data-act="quick" data-u="noa">Noa (noa@example.com)</a></p></div>';
  };

  function importHistory() {
    var rows = DB.history.filter(function (h) { return h.owner === S.user; }).map(function (h) {
      return '<tr><td>' + esc(h.file) + '</td><td class="mono">' + esc(h.hash) + '…</td><td>' + h.date + '</td><td>' + badge(h.status) + '</td><td>' + h.rows + '</td></tr>';
    }).join('');
    return '<div class="card"><h2>Import history</h2><table><tr><th>File</th><th>SHA-256</th><th>Date</th><th>Status</th><th>Rows imported</th></tr>' +
      (rows || '<tr><td colspan="5" class="muted">No imports yet.</td></tr>') + '</table></div>';
  }

  function stepper() {
    var r0 = function () { return DB.scenarios[S.sim]; };
    var names = ['Choose file', 'Dry-run preview', 'Identity', 'Confirm & import'];
    return '<div class="steps">' + names.map(function (n, i) {
      var k = i + 1, skipped = k === 3 && S.step > 3 && r0().exit !== 'identity';
      return '<div class="step ' + (S.step === k ? 'cur' : (S.step > k ? 'done' : '')) + '">' + (skipped ? '– Identity (not needed)' : (S.step > k ? '✓ ' : k + '. ') + n) + '</div>';
    }).join('') + '</div>';
  }

  function stats(r, wouldLabel) {
    return '<div class="grid g5"><div class="stat"><b>' + r.read + '</b>Rows read</div><div class="stat ok"><b>' + r.import + '</b>' + wouldLabel + '</div>' +
      '<div class="stat"><b>' + r.dup + '</b>Duplicates</div><div class="stat"><b>' + r.skip + '</b>Skipped</div><div class="stat warn"><b>' + r.review + '</b>Needs review</div></div>';
  }
  function warnIssues(r) {
    var h = r.warnings.map(function (w) { return '<div class="notice warn">⚠ ' + esc(w) + '</div>'; }).join('');
    if (r.issues.length) h += '<h3 style="margin-top:14px">Row issues</h3><table><tr>    <th>Row</th><th>Sheet</th><th>Severity</th><th>Problem</th></tr>' +
          r.issues.map(function (i) { return '<tr><td>' + i.row + '</td><td>Sheet ' + i.sheetPosition + '</td><td>' + badge(i.severity) + '</td><td>' + esc(i.message) + '</td></tr>'; }).join('') + '</table>';
    return h;
  }

  pages.import = function () {
    var h = '<h1>Import statement</h1><p class="muted">Max, Isracard and Cal .xlsx statements.</p>';
    h += '<div class="sim"><b>Simulate:</b> <select data-act="sim">' + Object.keys(DB.scenarios).map(function (k) {
      return '<option value="' + k + '"' + (S.sim === k ? ' selected' : '') + '>' + DB.scenarios[k].label + '</option>';
    }).join('') + '</select> <span class="muted">(prototype only — decides what the mock import returns)</span></div>';
    h += stepper() + '<div class="card">';
    var r = DB.scenarios[S.sim];
    if (S.step === 1) {
      h += '<div class="drop" id="drop"><p style="font-size:16px;margin-top:0">Drag & drop a statement here</p><p>or</p>' +
        '<button class="primary" data-act="pick">Choose .xlsx file</button><input type="file" id="fileIn" accept=".xlsx" hidden></div>';
      if (S.file) h += '<p>Selected: <b>' + esc(S.file.name) + '</b></p>';
      h += '<div class="row spread" style="margin-top:14px"><span>' + (S.file ? '' : '<span class="muted">No file selected</span>') +
        '</span><span class="row"><button data-act="useSample">Use sample file</button><button class="primary" data-act="toDry" ' + (S.file ? '' : 'disabled') + '>Run dry run →</button></span></div>';
    } else if (S.step === 2) {
      h += '<div class="row spread"><h2>Dry-run preview</h2><span><span class="badge b-success">Detected: ' + S.file.issuer + '</span> <span class="badge b-primary">DRY RUN – nothing saved</span></span></div>' +
        '<p class="muted">' + esc(S.file.name) + ' · SHA-256 ' + S.file.hash + '…</p>' + stats(r, 'Would import') + '<div style="height:12px"></div>' + warnIssues(r);
      if (r.exit === 'identity') h += '<div class="notice err">Identity must be chosen before this file can be imported.</div>';
      h += '<div class="row spread" style="margin-top:14px"><button data-act="restart">← Back</button><button class="primary" data-act="afterDry">' + (r.exit === 'identity' ? 'Choose identity →' : 'Continue →') + '</button></div>';
    } else if (S.step === 3) {
      h += '<h2>Which card is this?</h2><div class="notice">We found <b>' + S.file.issuer + ' ••••5530</b> in the file but could not match it automatically. Only the last 4 digits are shown.</div>' +
        '<label class="choice"><input type="radio" name="idm" value="existing"' + (S.identity.mode === 'existing' ? ' checked' : '') + '> Use an existing card ' +
        '<select data-act="idCard">' + myCards().map(function (c) { return '<option value="' + c.id + '"' + (S.identity.cardId === c.id ? ' selected' : '') + '>' + esc(cardName(c)) + '</option>'; }).join('') + '</select></label>' +
        '<label class="choice"><input type="radio" name="idm" value="new"' + (S.identity.mode === 'new' ? ' checked' : '') + '> Create a new card: ' + S.file.issuer + ' ••••5530</label>' +
        '<label class="f">Label / nickname (optional)<input type="text" id="idLabel" placeholder="e.g. Travel card" value="' + esc(S.identity.label) + '"></label>' +
        '<div class="row spread"><button class="danger" data-act="cancelId">Cancel import</button><button class="primary" data-act="idDone">Continue →</button></div>';
    } else if (S.step === 4) {
      if (!S.imported) {
        h += '<h2>Confirm import</h2>' + stats(r, 'Will import') + '<p class="muted">' + esc(S.file.name) + ' → ' +
          (r.exit === 'identity' ? (S.identity.mode === 'new' ? 'new card ••••5530' + (S.identity.label ? ' "' + esc(S.identity.label) + '"' : '') : 'existing card') : 'matched card') +
          '. This writes to your account.</p><div class="row spread"><button data-act="restart">Cancel</button><button class="primary" data-act="doImport">Import for real</button></div>';
      } else {
        var st = r.exit === 'partial' ? 'partial' : 'ok';
        h += '<div class="notice ' + (st === 'ok' ? 'ok' : 'warn') + '"><b>' + (st === 'ok' ? 'Import finished successfully' : 'Partially imported — some rows had errors') + '</b></div>' +
          stats(r, 'Imported') + '<div style="height:12px"></div>' + warnIssues(r) +
          '<div class="row" style="margin-top:14px">' + (r.review ? '<a class="btn primary" style="color:#fff" href="../review/index.html">Review ' + r.review + ' rows with unknown currency →</a>' : '') + '<button data-act="restart">Import another file</button></div>';
      }
    }
    return h + '</div>' + importHistory();
  };

  pages.cards = function () {
    var h = '<h1>Profile & cards</h1><div class="card row"><div class="avatar" style="width:48px;height:48px;font-size:20px">' + esc(me().name[0]) + '</div><div><b>' + esc(me().name) + '</b><div class="muted">' + esc(me().email) + ' · Owner</div></div></div>';
    h += '<h2>My cards</h2><div class="grid g2">' + myCards().map(function (c) {
      return '<div class="cardtile"><div class="row spread"><b>' + c.issuer + '</b><span class="badge b-primary">Owner: ' + esc(me().name) + '</span></div><div class="last4">•••• ' + c.last4 + '</div>' +
        '<div class="muted" style="font-size:12px;margin-bottom:8px">Last 4 digits only — full number is never stored or shown.</div>' +
        '<div class="row"><input type="text" id="nick-' + c.id + '" value="' + esc(c.nickname) + '" placeholder="Nickname" style="flex:1"><button data-act="saveNick" data-id="' + c.id + '">Save</button></div></div>';
    }).join('') + '</div>';
    return h;
  };

  // ---------- router ----------
  function render() {
    var route = (location.hash.replace(/^#\//, '') || '').split('?')[0];
    if (!S.user && route !== 'login') { location.hash = '#/login'; return; }
    if (S.user && (!route || route === 'login')) { location.hash = '#/import'; return; }
    if (!pages[route]) route = S.user ? 'import' : 'login';
    chrome(route);
    $('#view').innerHTML = pages[route]();
    var drop = $('#drop'), fi = $('#fileIn');
    if (drop) {
      ['dragover', 'dragenter'].forEach(function (e) { drop.addEventListener(e, function (ev) { ev.preventDefault(); drop.classList.add('over'); }); });
      drop.addEventListener('dragleave', function () { drop.classList.remove('over'); });
      drop.addEventListener('drop', function (ev) { ev.preventDefault(); setFile(ev.dataTransfer.files[0]); });
      fi.addEventListener('change', function () { setFile(fi.files[0]); });
    }
  }
  function setFile(f) {
    if (!f) return;
    if (!/\.xlsx$/i.test(f.name)) { toast('Only .xlsx files are supported'); return; }
    if (f.size > MAX_FILE_BYTES) { toast('File is too large (max 10 MB)'); return; }
    var n = f.name.toLowerCase();
    S.file = { name: f.name, hash: hashOf(f.name), issuer: n.indexOf('isra') >= 0 ? 'Isracard' : n.indexOf('cal') >= 0 ? 'Cal' : 'Max' };
    render();
  }
  function reset() { S.step = 1; S.file = null; S.imported = null; S.identity = { mode: 'existing', cardId: (myCards()[0] || {}).id || '', label: '' }; }

  function addHistory(status, rows) {
    DB.history.unshift({ file: S.file.name, hash: S.file.hash, date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16).replace('T', ' '), status: status, rows: rows, owner: S.user });
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-act]'); if (!el || el.tagName === 'SELECT') return;
    var a = el.dataset.act, r = DB.scenarios[S.sim];
    if (a === 'pick') { $('#fileIn').click(); return; }
    if (a === 'useSample') { setFile({ name: 'max_2026-10.xlsx' }); return; }
    if (a === 'toDry') S.step = 2;
    else if (a === 'restart' || a === 'cancelId') { if (a === 'cancelId') toast('Import cancelled — nothing was saved'); reset(); }
    else if (a === 'afterDry') S.step = r.exit === 'identity' ? 3 : 4;
    else if (a === 'idDone') { S.identity.label = ($('#idLabel') || {}).value || ''; S.step = 4; }
    else if (a === 'doImport') { S.imported = true; if (r.import > 0) addHistory(r.exit === 'partial' ? 'PARTIAL' : 'SUCCESS', r.import); }
    else if (a === 'login' || a === 'quick') {
      e.preventDefault();
      var u = a === 'quick' ? el.dataset.u : (Object.keys(DB.users).filter(function (k) { return DB.users[k].email === ($('#lemail').value || '').trim().toLowerCase(); })[0]);
      if (!u) { toast('Unknown mock user — try dan@example.com or noa@example.com'); return; }
      S.user = u; sessionStorage.setItem('proto.user', u); reset(); location.hash = '#/import'; return;
    }
    else if (a === 'logout') { S.user = null; sessionStorage.removeItem('proto.user'); location.hash = '#/login'; render(); return; }
    else if (a === 'saveNick') { DB.cards.filter(function (c) { return c.id === el.dataset.id; })[0].nickname = $('#nick-' + el.dataset.id).value.trim(); toast('Nickname saved'); }
    else return;
    render();
  });
  document.addEventListener('change', function (e) {
    var el = e.target;
    if (el.dataset.act === 'switchUser') { S.user = el.value; sessionStorage.setItem('proto.user', S.user); reset(); render(); toast('Now acting as ' + me().name); }
    else if (el.dataset.act === 'sim') { S.sim = el.value; if (S.step > 1) { S.step = S.file ? 2 : 1; S.imported = null; } render(); }
    else if (el.dataset.act === 'idCard') S.identity.cardId = el.value;
    else if (el.name === 'idm') { S.identity.mode = el.value; S.identity.label = ($('#idLabel') || {}).value || ''; render(); }
  });
  window.addEventListener('hashchange', render);
  reset(); render();
})();

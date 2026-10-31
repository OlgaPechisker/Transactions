/* Mock data generator (deterministic). Exposes window.DATA. */
(function () {
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const rnd = mulberry32(20260110);
  const pick = a => a[Math.floor(rnd() * a.length)];
  const between = (a, b) => a + rnd() * (b - a);
  const r2 = n => Math.round(n * 100) / 100;

  const addDays = (s, n) => {
    const [y, m, d] = s.split('-').map(Number);
    return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
  };
  const dayDiff = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);
  const nextCharge = s => {
    let [y, m, d] = s.split('-').map(Number);
    if (d >= 10) { m++; if (m > 12) { m = 1; y++; } }
    return `${y}-${String(m).padStart(2, '0')}-10`;
  };

  const CARDS = ['Olga Max ••••4396', 'Oleg Max ••••1182'];
  const BASE = { ILS: 1, USD: 3.28, EUR: 3.85, JPY: 0.0215 };

  const FOOD = 'מזון וצריכה', REST = 'מסעדות קפה וברים', LEIS = 'פנאי בידור וספורט',
    TRAVEL = 'טיסות ותיירות', TRANSP = 'תחבורה ורכבים', FUEL = 'דלק חשמל וגז', MISC = 'שונות';

  const txs = [];
  function add(o) {
    const cur = o.cur || 'ILS';
    const t = {
      txDate: o.date, chargeDate: o.type === 'IMMEDIATE_CHARGE' ? o.date : nextCharge(o.date),
      merchant: o.merchant, country: o.country || 'IL', city: o.city || '',
      bankCategory: o.cat, customCategory: o.custom || null,
      amount: 0, origAmount: null, origCurrency: cur,
      statedRate: null, effectiveRate: null, commission: 0,
      card: o.card || pick(CARDS), type: o.type || 'REGULAR',
      installment: o.installment || null, notes: o.notes || '', reviewReasons: [],
      tripTag: o.tripTag || null, chargeOverride: o.chargeOverride
    };
    if (cur === 'unknown') {
      t.amount = o.ils;
      t.reviewReasons.push('UNKNOWN_CURRENCY');
    } else if (cur === 'ILS') {
      t.amount = o.ils; t.origAmount = o.ils; t.statedRate = 1; t.effectiveRate = 1;
    } else {
      t.origAmount = o.orig;
      const base = BASE[cur] * (1 + (o.rateShift || 0));
      t.statedRate = +(base * (1 + between(-0.011, 0.011))).toFixed(4);
      const neg = o.orig < 0 ? -1 : 1;
      const core = o.orig * t.statedRate;
      t.commission = r2(Math.abs(core) * 0.01 * neg);
      t.amount = r2(core + t.commission);
      t.effectiveRate = +(t.amount / t.origAmount).toFixed(5);
    }
    txs.push(t);
    return t;
  }

  /* ---------- everyday local spending (Nov 2025 – Mar 2026) ---------- */
  const T = [
    ['שופרסל דיל', FOOD, 'תל אביב', 80, 520, 6], ['רמי לוי', FOOD, 'רמת גן', 150, 780, 5],
    ['ויקטורי', FOOD, 'גבעתיים', 60, 400, 3], ['יינות ביתן', FOOD, 'תל אביב', 90, 450, 3], ['AM:PM', FOOD, 'תל אביב', 15, 70, 2],
    ['ארומה אספרסו בר', REST, 'תל אביב', 22, 85, 5], ['קפה גרג', REST, 'רמת גן', 30, 140, 3],
    ['וולט', REST, 'תל אביב', 70, 210, 4], ['מסעדת מנטה ריי', REST, 'תל אביב', 180, 620, 3],
    ['סינמה סיטי', LEIS, 'ראשון לציון', 60, 160, 2], ['הפועל תל אביב - כרטיסים', LEIS, 'תל אביב', 120, 400, 1],
    ['פז תחנת דלק', FUEL, 'חולון', 150, 380, 4], ['סונול', FUEL, 'רמת גן', 150, 350, 3],
    ['רב-קו טעינה', TRANSP, 'תל אביב', 50, 200, 2], ['Gett', TRANSP, 'תל אביב', 30, 110, 2], ['חניון אחוזת החוף', TRANSP, 'תל אביב', 25, 90, 2],
    ['פוקס', MISC, 'רמת גן', 120, 420, 2], ['סופר-פארם', MISC, 'תל אביב', 40, 260, 3], ['איקאה', MISC, 'נתניה', 90, 1900, 1]
  ];
  const FT = [
    ['Amazon.com', MISC, 'Seattle', 'US', 'USD', 12, 120, 2],
    ['AliExpress', MISC, 'Hangzhou', 'CN', 'USD', 5, 40, 1]
  ];
  const wheel = [];
  T.forEach((t, i) => { for (let k = 0; k < t[5]; k++) wheel.push(i); });
  FT.forEach((t, i) => { for (let k = 0; k < t[7]; k++) wheel.push(100 + i); });
  for (let i = 0; i < 36; i++) {
    const idx = pick(wheel);
    const date = addDays('2025-11-01', Math.floor(rnd() * 150));
    if (idx >= 100) {
      const f = FT[idx - 100];
      add({ date, merchant: f[0], cat: f[1], city: f[2], country: f[3], cur: f[4], orig: r2(between(f[5], f[6])) });
    } else {
      const t = T[idx];
      add({ date, merchant: t[0], cat: t[1], city: t[2], ils: r2(between(t[3], t[4])) });
    }
  }

  /* ---------- recurring ---------- */
  ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03'].forEach(ym => {
    add({ date: ym + '-05', merchant: 'Netflix', cat: LEIS, city: 'Los Gatos', country: 'US', ils: 49.9, type: 'RECURRING', card: CARDS[0] });
    add({ date: ym + '-12', merchant: 'Spotify', cat: LEIS, city: 'Stockholm', ils: 19.9, type: 'RECURRING', card: CARDS[1] });
    add({ date: ym + '-01', merchant: 'הולמס פלייס', cat: LEIS, city: 'תל אביב', ils: 289, type: 'RECURRING', card: CARDS[0], custom: 'Health & fitness' });
  });
  [['2025-11-20', 412.3], ['2026-01-20', 538.9], ['2026-03-20', 460.15]].forEach(([date, ils]) =>
    add({ date, merchant: 'חברת החשמל', cat: FUEL, city: 'חיפה', ils, type: 'RECURRING', card: CARDS[0], notes: 'Bi-monthly bill' }));

  /* ---------- installments ---------- */
  ['2025-11', '2025-12', '2026-01', '2026-02', '2026-03'].forEach((ym, i) =>
    add({ date: '2025-09-14', merchant: 'iDigital - MacBook Air', cat: MISC, city: 'תל אביב', ils: 450, type: 'INSTALLMENTS', card: CARDS[0], notes: 'Laptop, 10 payments',
      installment: { n: 3 + i, total: 10, planTotal: 4500 }, chargeOverride: ym + '-10' }));
  ['2026-01', '2026-02', '2026-03'].forEach((ym, i) =>
    add({ date: '2025-12-30', merchant: 'Electra - מקרר', cat: MISC, city: 'פתח תקווה', ils: 650, type: 'INSTALLMENTS', card: CARDS[1],
      installment: { n: 1 + i, total: 6, planTotal: 3900 }, chargeOverride: ym + '-10' }));

  /* ---------- immediate charge, refunds, duplicates ---------- */
  add({ date: '2025-12-01', merchant: 'ביטוח ישיר - ביטוח רכב', cat: TRANSP, city: 'תל אביב', ils: 1450, type: 'IMMEDIATE_CHARGE', card: CARDS[0] });
  add({ date: '2026-02-03', merchant: 'מכבי שירותי בריאות', cat: MISC, city: 'תל אביב', ils: 380, type: 'IMMEDIATE_CHARGE', card: CARDS[1] });
  add({ date: '2025-12-22', merchant: 'פוקס', cat: MISC, city: 'רמת גן', ils: -199, notes: 'Return – wrong size' });
  add({ date: '2026-02-18', merchant: 'Amazon.com', cat: MISC, city: 'Seattle', country: 'US', cur: 'USD', orig: -34.5, notes: 'Refund' });
  add({ date: '2026-01-14', merchant: 'וולט', cat: REST, city: 'תל אביב', ils: 96.5 });
  add({ date: '2026-01-14', merchant: 'וולט', cat: REST, city: 'תל אביב', ils: 96.5 });
  add({ date: '2026-02-14', merchant: 'מסעדת מנטה ריי', cat: REST, city: 'תל אביב', ils: 540, custom: 'Date night' });
  add({ date: '2026-03-08', merchant: 'פרחי השדה', cat: MISC, city: 'תל אביב', ils: 220, custom: 'Gifts' });
  add({ date: '2025-12-12', merchant: 'ויקטורי', cat: FOOD, city: 'גבעתיים', ils: 211.4, custom: 'Groceries' });
  add({ date: '2026-03-12', merchant: 'שופרסל דיל', cat: FOOD, city: 'תל אביב', ils: 342.8, custom: 'Groceries' });
  add({ date: '2026-02-26', merchant: 'PAYPAL *EBAY', cat: MISC, city: '', country: 'US', cur: 'unknown', ils: 187.4 });

  /* ---------- NYC conference trip (USD) ---------- */
  const NYC = 'nyc-conf';
  add({ date: '2025-10-25', merchant: 'אל על - טיסה לניו יורק', cat: TRAVEL, city: 'לוד', ils: 3890, tripTag: NYC, card: CARDS[1] });
  add({ date: '2025-11-03', merchant: 'Pod 51 Hotel', cat: TRAVEL, city: 'New York', country: 'US', cur: 'USD', orig: 890, card: CARDS[1] });
  add({ date: '2025-11-04', merchant: "Joe's Pizza", cat: REST, city: 'New York', country: 'US', cur: 'USD', orig: 12.5, card: CARDS[1] });
  add({ date: '2025-11-05', merchant: 'Uber *Trip', cat: TRANSP, city: 'New York', country: 'US', cur: 'USD', orig: 38.2, card: CARDS[1] });
  add({ date: '2025-11-06', merchant: 'Chelsea Market', cat: REST, city: 'New York', country: 'US', cur: 'USD', orig: 41.7, card: CARDS[1] });
  add({ date: '2025-11-07', merchant: 'MoMA', cat: LEIS, city: 'New York', country: 'US', cur: 'USD', orig: 60, card: CARDS[1] });
  add({ date: '2025-11-08', merchant: 'Best Buy', cat: MISC, city: 'New York', country: 'US', cur: 'USD', orig: 129.99, card: CARDS[1], custom: 'Electronics' });

  /* ---------- Rome weekend (EUR) ---------- */
  const ROME = 'rome-weekend';
  add({ date: '2026-01-15', merchant: 'Wizz Air', cat: TRAVEL, city: 'Budapest', country: 'IT', ils: 1380, tripTag: ROME, card: CARDS[0] });
  [['Hotel Artemide Roma', TRAVEL, 540], ['Trattoria da Enzo', REST, 62], ['Taxi Roma', TRANSP, 48], ['Musei Colosseo', LEIS, 32],
    ['Pizzarium Bonci', REST, 18], ['Gelateria del Teatro', REST, 9], ['Musei Vaticani', LEIS, 40]].forEach(([m, c, e], i) =>
    add({ date: addDays('2026-02-12', Math.min(3, i)), merchant: m, cat: c, city: 'Roma', country: 'IT', cur: 'EUR', orig: e, card: CARDS[0] }));

  /* ---------- Japan winter trip (JPY) ---------- */
  const JP = 'japan-winter';
  const jp = (date, merchant, cat, city, yen, extra) =>
    add(Object.assign({ date, merchant, cat, city, country: 'JP', cur: 'JPY', orig: yen, card: CARDS[yen % 2] }, extra));
  add({ date: '2025-11-18', merchant: 'אל על - טיסה לטוקיו (1/2)', cat: TRAVEL, city: 'לוד', ils: 4820, tripTag: JP, card: CARDS[0] });
  add({ date: '2025-11-18', merchant: 'אל על - טיסה לטוקיו (2/2)', cat: TRAVEL, city: 'לוד', ils: 4820, tripTag: JP, card: CARDS[1] });
  add({ date: '2025-12-09', merchant: 'הראל - ביטוח נסיעות', cat: TRAVEL, city: 'תל אביב', ils: 640, tripTag: JP, card: CARDS[0] });
  add({ date: '2025-12-10', merchant: 'מונית נתב"ג', cat: TRANSP, city: 'לוד', ils: 210, tripTag: JP, card: CARDS[0] });
  add({ date: '2025-12-14', merchant: 'Klook - teamLab & Ghibli tickets', cat: LEIS, city: 'Hong Kong', country: 'CN', cur: 'USD', orig: 96, tripTag: JP, card: CARDS[0] });
  jp('2025-12-05', 'JR Pass – online order', TRAVEL, 'Tokyo', 52000, { tripTag: JP, notes: 'Charged before departure, so it is outside the trip dates' });
  jp('2025-11-27', 'Amazon.co.jp', MISC, 'Tokyo', 4380, { notes: 'Not trip related' });
  jp('2026-01-07', 'Narita Airport – duty free', MISC, 'Narita', 18600, { notes: 'Posted late, after the trip ended' });
  add({ date: '2025-12-27', merchant: 'Trip.com', cat: TRAVEL, city: 'Singapore', country: 'JP', cur: 'unknown', ils: 1236, card: CARDS[0] });

  jp('2025-12-16', 'Hotel Gracery Shinjuku', TRAVEL, 'Tokyo', 168000, { custom: 'Lodging' });
  jp('2025-12-17', 'Hakone Ginyu Ryokan', TRAVEL, 'Hakone', 160000, { custom: 'Lodging' });
  jp('2025-12-23', 'Kyoto Granbel Hotel', TRAVEL, 'Kyoto', 142000, { custom: 'Lodging' });
  jp('2025-12-28', 'Cross Hotel Osaka', TRAVEL, 'Osaka', 96000, { custom: 'Lodging' });
  jp('2026-01-05', 'Hotel Gracery Shinjuku', TRAVEL, 'Tokyo', 212000, { custom: 'Lodging' });
  jp('2025-12-18', 'JR East – Shinkansen', TRANSP, 'Tokyo', 14500);
  jp('2025-12-24', 'JR West – Shinkansen', TRANSP, 'Kyoto', 13900);
  jp('2025-12-25', 'Universal Studios Japan', LEIS, 'Osaka', 18800);
  jp('2025-12-12', 'teamLab Planets', LEIS, 'Tokyo', 7600);
  jp('2025-12-31', 'Bic Camera Shinjuku', MISC, 'Tokyo', 198000, { tripTag: JP, notes: 'Camera lens, tax-free' });
  jp('2025-12-10', 'Pocket WiFi Rental', MISC, 'Tokyo', 9800);

  const daily = {
    food: [['7-Eleven', 'Tokyo', 400, 1500], ['FamilyMart', 'Tokyo', 400, 1400], ['Lawson', 'Tokyo', 350, 1300]],
    eat: [['Ichiran Ramen', 1100, 1800], ['Sushiro', 1800, 4500], ['Matsuya', 600, 1200], ['Starbucks', 700, 1600],
      ['Izakaya Torikizoku', 3500, 8000], ['Sukiyabashi Jiro Roppongi', 15000, 28000], ['Tsukiji Outer Market', 2000, 6500], ['Kyoto Kaiseki Gion', 12000, 22000]],
    move: [['Suica top-up', 1000, 5000], ['Tokyo Metro', 200, 1200], ['Kyoto City Bus', 230, 1000], ['Taxi', 1500, 6000]],
    fun: [['Tokyo Skytree', 2100, 3500], ['Nara Park – deer crackers & tickets', 800, 2500], ['Fushimi Inari Souvenirs', 1500, 4000], ['Kinkaku-ji Temple', 500, 1200]],
    shop: [['Don Quijote', 3000, 22000], ['Uniqlo Ginza', 4000, 16000], ['MUJI', 2500, 12000], ['Pokémon Center', 3000, 14000]]
  };
  const jpCity = i => (i <= 6 ? 'Tokyo' : i === 7 ? 'Hakone' : i <= 13 ? 'Kyoto' : i <= 18 ? 'Osaka' : 'Tokyo');
  for (let i = 0; i <= 26; i++) {
    const date = addDays('2025-12-10', i), city = jpCity(i);
    let f = pick(daily.food);
    if (i % 3 !== 2) jp(date, f[0], FOOD, city, Math.round(between(f[2], f[3]) / 10) * 10);
    let e = pick(daily.eat);
    if (i % 2 === 0) jp(date, e[0], REST, city, Math.round(between(e[1], e[2]) / 10) * 10);
    if (rnd() < 0.3) { const m = pick(daily.move); jp(date, m[0], TRANSP, city, Math.round(between(m[1], m[2]) / 10) * 10); }
    if (i % 6 === 1) { const s = pick(daily.fun); jp(date, s[0], LEIS, city, Math.round(between(s[1], s[2]) / 10) * 10); }
    if (i % 9 === 3) { const s = pick(daily.shop); jp(date, s[0], MISC, city, Math.round(between(s[1], s[2]) / 10) * 10); }
  }

  /* ---------- finalize ---------- */
  txs.forEach(t => { if (t.chargeOverride) { t.chargeDate = t.chargeOverride; delete t.chargeOverride; } });
  txs.sort((a, b) => a.txDate.localeCompare(b.txDate) || a.merchant.localeCompare(b.merchant));
  txs.forEach((t, i) => { t.id = 't' + String(i + 1).padStart(3, '0'); });

  const trips = [
    { id: 'japan-winter', name: 'Winter trip in Japan', currency: 'JPY', start: '2025-12-10', end: '2026-01-05', excluded: [] },
    { id: 'rome-weekend', name: 'Rome weekend', currency: 'EUR', start: '2026-02-12', end: '2026-02-15', excluded: [] },
    { id: 'nyc-conf', name: 'NYC conference', currency: 'USD', start: '2025-11-03', end: '2025-11-09', excluded: [] }
  ];

  window.DATA = {
    transactions: txs, trips, cards: CARDS, addDays, dayDiff
  };
})();

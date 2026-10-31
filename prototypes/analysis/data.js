/* Mock data generator: deterministic (seeded) so the prototype looks the same on every open.
   Exposes window.MOCK = { TODAY, transactions, CUSTOM_CATEGORIES }.
   Fields: id, date, chargeDate, merchant, country, cat (bank, Hebrew), custom (user override or ''),
           ils (billing amount, negative = refund), orig, cur, card, type, trip, inst ('2/3' or ''). */
(function () {
  const TODAY = '2026-10-29'; // the server's "today" in UTC (Q-9); all date math in app.js is UTC
  const CARDS = ['Olga Max ••••4396', 'Alex Max ••••1182'];
  const CUSTOM_CATEGORIES = ['Groceries', 'Takeout', 'Kids', 'Gifts', 'Parking', 'Japan trip', 'Italy trip'];

  let seed = 20261029;
  const rnd = () => {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const between = (a, b) => a + rnd() * (b - a);
  const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
  const pad = (n) => String(n).padStart(2, '0');
  const iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
  const daysIn = (y, m) => new Date(Date.UTC(y, m, 0)).getUTCDate();
  const RATE = { ILS: () => 1, USD: () => between(3.5, 3.75), EUR: () => between(3.95, 4.25), JPY: () => between(0.0235, 0.0252) };

  const M = {
    food: 'מזון וצריכה', rest: 'מסעדות קפה וברים', fun: 'פנאי בידור וספורט', travel: 'טיסות ותיירות',
    transport: 'תחבורה ורכבים', fuel: 'דלק חשמל וגז', misc: 'שונות', home: 'עיצוב הבית',
    health: 'רפואה ובתי מרקחת', fashion: 'אופנה'
  };

  // [merchant, category, country, currency, min, max, weight, extras]
  const REGULAR = [
    ['שופרסל דיל', M.food, 'IL', 'ILS', 80, 600, 6], ['רמי לוי שיווק השקמה', M.food, 'IL', 'ILS', 100, 700, 4],
    ['ויקטורי', M.food, 'IL', 'ILS', 50, 400, 2], ['AM:PM', M.food, 'IL', 'ILS', 15, 80, 2],
    ['ארומה תל אביב', M.rest, 'IL', 'ILS', 25, 90, 4], ['קפה גרג', M.rest, 'IL', 'ILS', 30, 110, 3],
    ['מקדונלדס', M.rest, 'IL', 'ILS', 40, 130, 2], ['וולט Wolt', M.rest, 'IL', 'ILS', 60, 200, 3, { custom: 'Takeout', p: 0.7 }],
    ['פז תחנת דלק', M.fuel, 'IL', 'ILS', 180, 420, 5], ['סונול', M.fuel, 'IL', 'ILS', 170, 380, 3], ['דור אלון', M.fuel, 'IL', 'ILS', 150, 350, 2],
    ['פנגו חניה', M.transport, 'IL', 'ILS', 15, 70, 2, { type: 'IMMEDIATE_CHARGE', custom: 'Parking', p: 0.8 }],
    ['גט טקסי', M.transport, 'IL', 'ILS', 35, 120, 2, { type: 'IMMEDIATE_CHARGE' }],
    ['סופר-פארם', M.health, 'IL', 'ILS', 40, 300, 3, { custom: 'Kids', p: 0.3 }], ['מכבי שירותי בריאות', M.health, 'IL', 'ILS', 30, 150, 1],
    ['זארה', M.fashion, 'IL', 'ILS', 150, 600, 1.5], ['קסטרו', M.fashion, 'IL', 'ILS', 100, 400, 1.5],
    ['ACE', M.home, 'IL', 'ILS', 40, 400, 1], ['סינמה סיטי', M.fun, 'IL', 'ILS', 60, 180, 1.5],
    ['דואר ישראל', M.misc, 'IL', 'ILS', 15, 80, 1], ['עיריית תל אביב', M.misc, 'IL', 'ILS', 200, 600, 0.3],
    ['Amazon.com', M.misc, 'US', 'USD', 20, 150, 1.2, { custom: 'Gifts', p: 0.3 }], ['Steam Games', M.fun, 'US', 'USD', 10, 60, 0.6],
    ['Booking.com', M.travel, 'NL', 'EUR', 100, 400, 0.4]
  ];
  const RECURRING = [
    ['Netflix', M.fun, 'US', 'USD', 13, 7], ['הולמס פלייס', M.fun, 'IL', 'ILS', 289, 3], ['Spotify', M.fun, 'SE', 'EUR', 5, 12]
  ];
  const JAPAN = [
    ['Ichiran Ramen', M.rest, 1200, 3500, 3], ['7-Eleven Japan', M.food, 400, 1800, 3], ['Lawson', M.food, 300, 1500, 2],
    ['JR East', M.transport, 1500, 6000, 2], ['Tokyo Metro', M.transport, 200, 1200, 2], ['teamLab Planets', M.fun, 3500, 7000, 1],
    ['Shibuya Sky', M.fun, 2000, 4000, 1], ['Uniqlo Ginza', M.fashion, 4000, 15000, 1], ['Don Quijote', M.misc, 3000, 20000, 1.5]
  ];
  const ITALY = [
    ['Trattoria Da Enzo', M.rest, 30, 90, 3], ['Gelateria del Teatro', M.rest, 4, 15, 2], ['Roma Metro', M.transport, 2, 20, 2],
    ['Musei Vaticani', M.fun, 20, 60, 1], ['Ryanair', M.travel, 120, 300, 1]
  ];

  const txs = [];
  const add = (o) => {
    const rate = RATE[o.cur]();
    const abs = Math.round(o.orig * rate * 100) / 100;
    const [y, m] = o.date.split('-').map(Number);
    const ny = m === 12 ? y + 1 : y, nm = m === 12 ? 1 : m + 1;
    txs.push({
      date: o.date, chargeDate: o.type === 'IMMEDIATE_CHARGE' ? o.date : iso(ny, nm, 2), merchant: o.merchant, country: o.country,
      cat: o.cat, custom: o.custom || '', ils: o.refund ? -abs : abs, orig: o.refund ? -o.orig : o.orig, cur: o.cur,
      card: o.card || pick(CARDS), type: o.type || 'REGULAR', trip: o.trip || '', inst: o.inst || ''
    });
  };
  const wpick = (list, w) => {
    let r = rnd() * list.reduce((s, x) => s + w(x), 0);
    for (const x of list) { if ((r -= w(x)) <= 0) return x; }
    return list[0];
  };
  const round = (cur, v) => (cur === 'JPY' ? Math.round(v / 10) * 10 : Math.round(v * 100) / 100);
  const randomDay = (y, m) => iso(y, m, 1 + Math.floor(rnd() * (y === 2026 && m === 10 ? 29 : daysIn(y, m))));

  // 24 months: the older 12 are lighter and only there to make year-over-year comparison meaningful
  for (let i = 0; i < 24; i++) {
    const y = 2024 + Math.floor((10 + i) / 12), m = ((10 + i) % 12) + 1;
    const n = i >= 12 ? 9 : 6;
    for (let k = 0; k < n; k++) {
      const [name, cat, country, cur, lo, hi, , ex = {}] = wpick(REGULAR, (x) => x[6]);
      add({
        date: randomDay(y, m), merchant: name, cat, country, cur, orig: round(cur, between(lo, hi)),
        type: ex.type, custom: ex.custom && rnd() < (ex.p || 1) ? ex.custom : ''
      });
    }
    RECURRING.forEach(([name, cat, country, cur, amt, day]) => add({ date: iso(y, m, day), merchant: name, cat, country, cur, orig: amt, type: 'RECURRING', card: CARDS[0] }));
  }

  // Japan trip Dec 2025 - Jan 2026 (JPY)
  const JP_TRIP = 'Japan Dec 2025';
  for (let k = 0; k < 16; k++) {
    const [name, cat, lo, hi] = wpick(JAPAN, (x) => x[4]);
    const day = Math.floor(between(0, 20));
    const d = day < 18 ? iso(2025, 12, 14 + day) : iso(2026, 1, day - 17);
    add({ date: d, merchant: name, cat, country: 'JP', cur: 'JPY', orig: round('JPY', between(lo, hi)), trip: JP_TRIP, custom: 'Japan trip', card: CARDS[0] });
  }
  add({ date: '2025-12-15', merchant: 'Hotel Gracery Shinjuku', cat: M.travel, country: 'JP', cur: 'JPY', orig: 182000, trip: JP_TRIP, custom: 'Japan trip', card: CARDS[0] });
  add({ date: '2025-12-21', merchant: 'Hoshinoya Tokyo', cat: M.travel, country: 'JP', cur: 'JPY', orig: 96000, trip: JP_TRIP, custom: 'Japan trip', card: CARDS[0] });
  ['2025-11-05', '2025-12-05', '2026-01-05'].forEach((d, i) => add({ date: d, merchant: 'אל על', cat: M.travel, country: 'IL', cur: 'ILS', orig: 1280, type: 'INSTALLMENTS', inst: `${i + 1}/3`, trip: JP_TRIP, custom: 'Japan trip', card: CARDS[0] }));

  // Italy trip Jul 2026 (EUR)
  const IT_TRIP = 'Italy Jul 2026';
  for (let k = 0; k < 11; k++) {
    const [name, cat, lo, hi] = wpick(ITALY, (x) => x[4]);
    add({ date: iso(2026, 7, 8 + Math.floor(rnd() * 8)), merchant: name, cat, country: 'IT', cur: 'EUR', orig: round('EUR', between(lo, hi)), trip: IT_TRIP, custom: 'Italy trip', card: CARDS[1] });
  }
  add({ date: '2026-07-08', merchant: 'Hotel Roma Centro', cat: M.travel, country: 'IT', cur: 'EUR', orig: 640, trip: IT_TRIP, custom: 'Italy trip', card: CARDS[1] });

  // IKEA purchase split into 3 installments
  ['2026-08-10', '2026-09-10', '2026-10-10'].forEach((d, i) => add({ date: d, merchant: 'איקאה', cat: M.home, country: 'IL', cur: 'ILS', orig: 800, type: 'INSTALLMENTS', inst: `${i + 1}/3`, card: CARDS[1] }));

  // Refunds
  [['2026-09-18', 'זארה', M.fashion, 'IL', 'ILS', 289], ['2026-10-05', 'Amazon.com', M.misc, 'US', 'USD', 42.5],
   ['2026-02-12', 'סופר-פארם', M.health, 'IL', 'ILS', 76], ['2026-08-02', 'Booking.com', M.travel, 'NL', 'EUR', 120],
   ['2026-05-20', 'קסטרו', M.fashion, 'IL', 'ILS', 199], ['2026-01-14', 'Don Quijote', M.misc, 'JP', 'JPY', 8800]]
    .forEach(([date, merchant, cat, country, cur, orig]) => {
      const jp = cur === 'JPY';
      add({ date, merchant, cat, country, cur, orig, refund: true, trip: jp ? JP_TRIP : '', custom: jp ? 'Japan trip' : '' });
    });

  txs.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  txs.forEach((t, i) => { t.id = 't' + String(i + 1).padStart(3, '0'); });
  window.MOCK = { TODAY, transactions: txs, CUSTOM_CATEGORIES };
})();

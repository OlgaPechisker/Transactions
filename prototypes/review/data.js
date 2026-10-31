// Deterministic mock data (static). Real app would read this from the importer.
(function () {
  let seed = 20261029;
  const rnd = () => (seed = (seed * 1664525 + 1013904223) % 4294967296) / 4294967296;
  const pick = (a) => a[Math.floor(rnd() * a.length)];
  const between = (a, b) => a + rnd() * (b - a);
  const pad = (n) => String(n).padStart(2, "0");
  const randDate = (from, to) => {
    const d = new Date(from + rnd() * (to - from));
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  };
  const D1 = Date.UTC(2025, 3, 1), D2 = Date.UTC(2026, 8, 28);

  const JP = ["JR EAST SIBUYAKU JP", "LAWSON SHINJUKU JP", "FAMILYMART TOKYO JP", "SEVEN ELEVEN OSAKA JP", "UNIQLO GINZA JP",
    "DON QUIJOTE SHIBUYA JP", "TOKYO METRO JP", "KYOTO STATION KIOSK JP", "ICHIRAN RAMEN JP", "TEAMLAB BORDERLESS JP",
    "BIC CAMERA AKIBA JP", "MUJI HARAJUKU JP", "OSAKA CASTLE TICKETS JP", "HANKYU RAILWAY JP", "SUSHIRO UENO JP"];
  const JP_RANGE = [[300, 1800], [1000, 6000], [2000, 20000], [5000, 60000]];
  const OTHER = [
    // merchant, country, currency-ish rate center, spread
    ["AMAZON.COM SEATTLE US", "US", 3.62, 0.1], ["UBER *TRIP NEW YORK US", "US", 3.6, 0.1], ["NETFLIX.COM LOS GATOS US", "US", 3.65, 0.08],
    ["BOOKING.COM AMSTERDAM NL", "NL", 4.02, 0.08], ["AIRBNB PARIS FR", "FR", 4.05, 0.08], ["LIDL BERLIN DE", "DE", 4.0, 0.06],
    ["BRITISH MUSEUM LONDON GB", "GB", 4.6, 0.1], ["TESCO LONDON GB", "GB", 4.55, 0.1],
    ["PAYPAL *EBAY LUXEMBOURG LU", "LU", 3.8, 0.04], // ambiguous USD/EUR
    ["7-ELEVEN BANGKOK TH", "TH", 0.105, 0.005], ["ALIEXPRESS SINGAPORE SG", "SG", 2.7, 0.05]
  ];
  const sizeFor = (rate) => rate > 3 ? [10, 400] : [5, 80];

  const pending = [];
  let id = 1;
  for (let i = 0; i < 346; i++) {
    const r = pick(JP_RANGE);
    const orig = Math.round(between(r[0], r[1]) / 10) * 10;
    const rate = i === 0 ? 0.0209 : (rnd() < 0.9 ? between(0.0196, 0.0216) : between(0.0121, 0.0195));
    pending.push({ id: "p" + id++, date: i === 0 ? "2026-09-14" : randDate(D1, D2), merchant: i === 0 ? JP[0] : pick(JP), country: "JP",
      orig: i === 0 ? 4000 : orig, ils: i === 0 ? 83.67 : +(orig * rate).toFixed(2), rate: +rate.toFixed(4) });
  }
  for (let i = 0; i < 28; i++) {
    const o = OTHER[i % OTHER.length];
    const rate = between(o[2] - o[3], o[2] + o[3]);
    const s = sizeFor(rate);
    const orig = Math.round(between(s[0], s[1]) * 100) / 100;
    pending.push({ id: "p" + id++, date: randDate(D1, D2), merchant: o[0], country: o[1], orig, ils: +(orig * rate).toFixed(2), rate: +rate.toFixed(4) });
  }
  pending.sort((a, b) => b.date.localeCompare(a.date));

  const FUEL = "דלק חשמל וגז", TRANS = "תחבורה ורכבים";
  const petrol = [
    ["PAZ YELLOW HERZLIYA", FUEL], ["פז תחנת דלק רעננה", FUEL], ["SONOL PETAH TIKVA", FUEL], ["סונול כפר סבא", FUEL],
    ["DELEK RAANANA", FUEL], ["דלק מנטה תל אביב", FUEL], ["TEN GAS STATION HOD HASHARON", FUEL], ["DOR ALON AM:PM NETANYA", FUEL],
    ["PAZ SUPER YUDA", TRANS], ["SONOL EXPRESS", TRANS]
  ];
  const general = [
    ["שופרסל דיל", "מזון וצריכה", 90, 600], ["רמי לוי שיווק השקמה", "מזון וצריכה", 120, 700], ["VICTORY SUPERMARKET", "מזון וצריכה", 60, 450],
    ["מקדונלדס", "מסעדות קפה וברים", 40, 160], ["ארומה אספרסו בר", "מסעדות קפה וברים", 20, 90], ["CAFE CAFE", "מסעדות קפה וברים", 30, 140],
    ["סינמה סיטי", "פנאי בידור וספורט", 50, 220], ["HOLMES PLACE", "פנאי בידור וספורט", 180, 420],
    ["EL AL ISRAEL AIRLINES", "טיסות ותיירות", 600, 3200], ["ISSTA TRAVEL", "טיסות ותיירות", 300, 2500],
    ["רב קו", "תחבורה ורכבים", 20, 120], ["GETT TAXI", "תחבורה ורכבים", 25, 110],
    ["חברת החשמל", FUEL, 180, 650], ["סופרגז", FUEL, 70, 160],
    ["ACE HOME", "עיצוב הבית", 60, 900], ["איקאה", "עיצוב הבית", 120, 1800],
    ["סופר פארם", "קוסמטיקה וטיפוח", 30, 260], ["מכבי שירותי בריאות", "רפואה ובתי מרקחת", 30, 300],
    ["CASTRO", "אופנה", 80, 500], ["סטימצקי", "ספרים ודפוס", 40, 180], ["ATM משיכת מזומן", "משיכת מזומן", 200, 1000],
    ["KSP מחשבים", "חשמל ומחשבים", 150, 2800], ["PAYBOX העברה", "שונות", 20, 400]
  ];
  const txns = [];
  let tid = 1;
  for (let i = 0; i < 95; i++) {
    const p = pick(petrol);
    txns.push({ id: "t" + tid++, date: randDate(D1, D2), merchant: p[0], bankCat: p[1], ils: +between(120, 420).toFixed(2) });
  }
  for (let i = 0; i < 205; i++) {
    const g = pick(general);
    txns.push({ id: "t" + tid++, date: randDate(D1, D2), merchant: g[0], bankCat: g[1], ils: +between(g[2], g[3]).toFixed(2) });
  }
  txns.sort((a, b) => b.date.localeCompare(a.date));

  window.MOCK = {
    TOTAL: pending.length, pending, txns,
    defaultCustomCats: ["Groceries", "Dining", "Petrol", "Utilities", "Transport", "Travel", "Entertainment", "Home", "Health", "Shopping", "Cash", "Other"]
  };
})();

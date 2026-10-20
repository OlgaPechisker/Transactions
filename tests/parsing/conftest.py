from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bank_transactions.domain.enums import IssuerCode, TransactionType
from bank_transactions.domain.issues import SourceLocation
from bank_transactions.domain.models import CardHintKey
from bank_transactions.domain.money import ILS, Money
from bank_transactions.domain.workbook import SheetView, WorkbookView
from bank_transactions.parsing.shared.draft import TransactionDraft
from bank_transactions.parsing.workbook import ExcelWorkbookReader

EXAMPLES_DIRECTORY = Path(__file__).resolve().parents[2] / "examples"

ISRACARD_SHEET = "פירוט עסקאות"
MAX_FOREIGN_SHEET = 'עסקאות חו"ל ומט"ח'
MAX_HEADER = (
    "תאריך עסקה",
    "שם בית העסק",
    "קטגוריה",
    "4 ספרות אחרונות של כרטיס האשראי",
    "סוג עסקה",
    "סכום חיוב",
    "מטבע חיוב",
    "סכום עסקה מקורי",
    "מטבע עסקה מקורי",
    "תאריך חיוב",
    "הערות",
    "תיוגים",
    "מועדון הנחות",
    "מפתח דיסקונט",
    "אופן ביצוע ההעסקה",
    'שער המרה ממטבע מקור/התחשבנות לש"ח',
)
MAX_DEFAULTS = (
    ("date", "01-06-2026"),
    ("merchant", "ACME"),
    ("category", "מזון וצריכה"),
    ("card", "1111"),
    ("type", "רגילה"),
    ("billing", 100.0),
    ("billing_currency", "₪"),
    ("original", 100.0),
    ("original_currency", "₪"),
    ("charge_date", "02-07-2026"),
    ("notes", None),
    ("tags", None),
    ("club", None),
    ("club_key", None),
    ("channel", "אינטרנט"),
    ("rate", None),
)
CAL_HEADER = (
    "תאריך\nעסקה",
    "שם בית עסק",
    "סכום\nעסקה",
    "סכום\nחיוב",
    "סוג\nעסקה",
    "ענף",
    "הערות",
)
CAL_DEFAULTS = (
    ("date", datetime(2026, 9, 3)),
    ("merchant", "ACME"),
    ("amount", 50.0),
    ("charge", 50.0),
    ("type", "רגילה"),
    ("category", "מסעדות"),
    ("notes", None),
)
MAX_DOMESTIC_SHEET = "עסקאות במועד החיוב"
ISRACARD_HEADER = (
    "תאריך רכישה",
    "שם בית עסק",
    "סכום עסקה",
    "מטבע עסקה",
    "סכום חיוב",
    "מטבע חיוב",
    "מס' שובר",
    "פירוט נוסף",
)
ISRACARD_DEFAULTS = (
    ("date", "09.02.26"),
    ("merchant", "ACME"),
    ("original", 100.0),
    ("original_currency", "₪"),
    ("billing", 100.0),
    ("billing_currency", "₪"),
    ("voucher", "839176964"),
    ("details", ""),
)


@pytest.fixture(scope="session")
def examples():
    """The four unmodified example workbooks, told apart by structure rather than file name."""
    reader = ExcelWorkbookReader()
    found = {}
    for path in sorted(EXAMPLES_DIRECTORY.glob("*.xlsx")):
        view = reader.open(path)
        if view.sheet_names == (ISRACARD_SHEET,):
            found["isracard"] = view
        elif MAX_DOMESTIC_SHEET in view.sheet_names:
            monthly = "/" in str(view.sheets[0].cell(3, 1))
            found["max_monthly" if monthly else "max_yearly"] = view
        else:
            found["cal"] = view
    assert set(found) == {"isracard", "cal", "max_monthly", "max_yearly"}
    return found


@pytest.fixture
def cal_row():
    """A Cal row in source column order; keywords override the defaults."""

    def build(**overrides):
        unknown = set(overrides) - {name for name, _ in CAL_DEFAULTS}
        assert not unknown, unknown
        return tuple(overrides.get(name, default) for name, default in CAL_DEFAULTS)

    return build


@pytest.fixture
def cal_workbook():
    """A one-sheet Cal export laid out like the real one, around the given rows."""

    def build(
        rows=(),
        title="פירוט עסקאות לחשבון לאומי לישראל 123-4567890 לכרטיס ויזה זהב עסקי המסתיים ב-4321",
        banner="עסקאות לחיוב ב-10/09/2026: 645.13 ₪",
        header=CAL_HEADER,
        sheet_name="לאומי לישראל 123-4567890",
    ):
        body = [
            (title,),
            (),
            (banner,),
            header,
            *rows,
            (),
            ("את המידע המלא על כל עסקה אפשר למצוא באתר",),
        ]
        return WorkbookView((SheetView(sheet_name, tuple(body)),))

    return build


@pytest.fixture
def max_row():
    """A Max row in source column order; keywords override the defaults."""

    def build(**overrides):
        unknown = set(overrides) - {name for name, _ in MAX_DEFAULTS}
        assert not unknown, unknown
        return tuple(overrides.get(name, default) for name, default in MAX_DEFAULTS)

    return build


@pytest.fixture
def max_workbook():
    """A two-sheet Max export laid out like the real one. A footer total of "auto" is the sum of
    the sheet's billed amounts; None leaves the footer out."""

    def sheet(name, rows, period, total):
        if total == "auto":
            billed = sum(Decimal(str(row[5])) for row in rows if isinstance(row[5], int | float))
            total = f"{billed}₪"
        footer = [] if total is None else [(None,) * 16, ("סך הכל",) + (None,) * 15, (total,)]
        banner = [("כל המשתמשים (2)",), ("כל הכרטיסים (2)",), (period,)]
        return SheetView(name, tuple([*banner, MAX_HEADER, *rows, *footer]))

    def build(
        domestic=(),
        foreign=(),
        period="07/2026",
        domestic_total="auto",
        foreign_total="auto",
        domestic_name=MAX_DOMESTIC_SHEET,
        foreign_name=MAX_FOREIGN_SHEET,
    ):
        return WorkbookView(
            (
                sheet(domestic_name, list(domestic), period, domestic_total),
                sheet(foreign_name, list(foreign), period, foreign_total),
            )
        )

    return build


@pytest.fixture
def isracard_row():
    """A statement row in source column order; keywords override the defaults."""

    def build(**overrides):
        unknown = set(overrides) - {name for name, _ in ISRACARD_DEFAULTS}
        assert not unknown, unknown
        return tuple(overrides.get(name, default) for name, default in ISRACARD_DEFAULTS)

    return build


@pytest.fixture
def isracard_workbook():
    """A one-sheet Isracard export laid out like the real one, around the given rows."""

    def build(
        rows=(),
        card="\u202bפלטינה מסטרקארד\u202c - 1234",
        owner="על שם דנה כהן",
        month="פברואר 2026",
        charge="לחיוב ב-10.02",
        totals=("₪ 100.00",),
        header=ISRACARD_HEADER,
        sheet_name=ISRACARD_SHEET,
        footer=True,
    ):
        empty = (None,) * 8

        def banner_row(first, last):
            return (first,) + (None,) * 6 + (last,)

        banner = [
            empty,
            ("פירוט עסקאות", None, month) + (None,) * 5,
            empty,
            empty,
            banner_row(card, totals[0] if totals else None),
            banner_row(owner, totals[1] if len(totals) > 1 else None),
            banner_row(None, charge),
            empty,
            ("מסגרת לכלל הכרטיסים",) + (None,) * 7,
            empty,
            empty,
            ("עסקאות למועד חיוב",) + (None,) * 7,
        ]
        tail = [
            (None, 'סה"כ לחיוב החודש בכרטיס') + (None,) * 6,
            (None, 'בש"ח', None, None, 100.0, "₪", None, None),
            empty,
            empty,
        ]
        legal = [("תנאים משפטיים\nהנתונים במסך זה אינם סופיים",) + (None,) * 7]
        body = [*banner, header, *rows, *(tail if footer else []), *legal]
        return WorkbookView((SheetView(sheet_name, tuple(body)),))

    return build


@pytest.fixture
def card_key():
    return CardHintKey(IssuerCode.MAX, "1111")


@pytest.fixture
def other_card_key():
    return CardHintKey(IssuerCode.MAX, "2222")


@pytest.fixture
def make_draft(card_key):
    def build(row=5, sheet=1, **overrides):
        values = {
            "card_key": card_key,
            "location": SourceLocation(sheet_position=sheet, row=row),
            "transaction_date": date(2026, 6, 1),
            "charge_date": date(2026, 7, 2),
            "billing": Money(Decimal("100.00"), ILS),
            "merchant_name_raw": "ACME",
            "transaction_type": TransactionType.REGULAR,
        }
        return TransactionDraft(**{**values, **overrides})

    return build

from bank_transactions.parsing.normalizers.text import (
    MerchantParts,
    cell_text,
    header_key,
    normalize_last4,
    parse_installment_note,
    split_merchant,
    strip_bidi_marks,
)


def test_bidi_embedding_marks_are_removed_from_banner_text():
    assert strip_bidi_marks("\u202bפלטינה מסטרקארד\u202c - 1234") == "פלטינה מסטרקארד - 1234"


def test_cell_text_trims_and_treats_blank_as_missing():
    assert cell_text("  הנחה ₪24.00 ") == "הנחה ₪24.00"
    assert cell_text("   ") is None
    assert cell_text(None) is None
    assert cell_text(12) == "12"


def test_multiline_header_collapses_to_one_line():
    assert header_key("תאריך\nעסקה") == "תאריך עסקה"
    assert header_key("  סכום \n\n חיוב ") == "סכום חיוב"


def test_header_key_unifies_apostrophe_variants_and_ignores_blank_cells():
    assert header_key("מס' שובר") == header_key("מס’ שובר")
    assert header_key(None) == ""


def test_fixed_width_name_city_country_is_split():
    parts = split_merchant("NETFLIX.COM            AMSTERDAM     NL")

    assert parts == MerchantParts(name="NETFLIX.COM", city="AMSTERDAM", country="NL")


def test_name_and_country_without_city_is_split_without_inventing_a_city():
    parts = split_merchant("OPENAI *CHATGPT SUBSCR OPENAI.COM    US")

    assert parts == MerchantParts(name="OPENAI *CHATGPT SUBSCR OPENAI.COM", city=None, country="US")


def test_single_space_country_suffix_is_not_confident_enough_to_split():
    raw = "HOTEL MONDAY AKIHABARA TAITOKUASAKUS JP"

    assert split_merchant(raw) == MerchantParts(name=raw, city=None, country=None)


def test_hebrew_and_trailing_space_names_are_left_whole():
    assert split_merchant("KLOOK TRAVEL TECH LTD    ").name == "KLOOK TRAVEL TECH LTD"
    assert split_merchant("אייר חיפה").country is None
    assert split_merchant('ספרינט מוטורס בע"מ- קריון').city is None


def test_lowercase_or_longer_tails_are_not_countries():
    assert split_merchant("SHOP   Us").country is None
    assert split_merchant("SHOP   USA").country is None


def test_installment_note_is_read_with_tolerant_wording():
    assert parse_installment_note("תשלום 4 מתוך 10") == (4, 10)
    assert parse_installment_note("  תשלום  1  מתוך  2 ") == (1, 2)
    assert parse_installment_note("תשלום מס' 3 מתוך 12") == (3, 12)
    assert parse_installment_note("תשלום 7/12") == (7, 12)
    assert parse_installment_note("הוראת קבע | תשלום 2 מתוך 6") == (2, 6)


def test_text_without_an_installment_pattern_has_no_installment():
    assert parse_installment_note("הוראת קבע") is None
    assert parse_installment_note(None) is None
    assert parse_installment_note("") is None


def test_out_of_range_installment_numbers_are_still_reported_for_the_caller_to_judge():
    assert parse_installment_note("תשלום 5 מתוך 3") == (5, 3)


def test_last4_keeps_leading_zeros_for_text_and_numeric_cells():
    assert normalize_last4("2468") == "2468"
    assert normalize_last4(123) == "0123"
    assert normalize_last4(" 0123 ") == "0123"
    assert normalize_last4(2468.0) == "2468"


def test_last4_rejects_anything_that_is_not_exactly_four_digits():
    assert normalize_last4("12345") is None
    assert normalize_last4("abcd") is None
    assert normalize_last4("") is None
    assert normalize_last4(None) is None
    assert normalize_last4(True) is None
    assert normalize_last4(-5) is None

import pytest

from bank_transactions.domain.text import (
    canonical_account_number,
    contains_identifier,
    fold_text,
)


def test_fold_text_trims_collapses_whitespace_and_casefolds():
    assert fold_text("  Ben   GURION\t Cafe ") == "ben gurion cafe"


def test_fold_text_unifies_hebrew_gershayim_and_geresh():
    assert fold_text("צה״ל") == fold_text('צה"ל')
    assert fold_text("ג׳ון") == fold_text("ג'ון")
    assert fold_text("o’neil") == fold_text("O'Neil")


def test_fold_text_keeps_hebrew_letters_unchanged():
    assert fold_text("פלטינה מסטרקארד") == "פלטינה מסטרקארד"


def test_canonical_account_number_removes_separators():
    assert canonical_account_number("891-7221038") == "8917221038"
    assert canonical_account_number(" 891 / 722.1038 ") == "8917221038"


@pytest.mark.parametrize("raw", ["", "---", "12ab34"])
def test_canonical_account_number_rejects_non_numeric_values(raw):
    with pytest.raises(ValueError):
        canonical_account_number(raw)


@pytest.mark.parametrize(
    "text",
    ["Gold 4396", "Gold 43-96", "Gold 4 3 9 6", "Gold 4.396 card", "x4396x"],
)
def test_a_label_repeating_the_card_digits_contains_an_identifier(text):
    assert contains_identifier(text, ["4396"])


def test_a_label_repeating_the_account_number_with_separators_contains_an_identifier():
    assert contains_identifier("main 891-7221038", ["8917221038"])
    assert contains_identifier("main 891 7221 038", ["8917221038"])


@pytest.mark.parametrize("text", ["Gold card", "Platinum 2026", "Dana Levi", ""])
def test_a_plain_label_contains_no_identifier(text):
    assert not contains_identifier(text, ["4396", "8917221038"])


def test_an_account_number_is_only_found_once_separators_are_removed():
    assert not contains_identifier("8917 pocket 221038", ["8917221038"])

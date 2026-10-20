import pytest

from bank_transactions.domain.text import canonical_account_number, fold_text


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

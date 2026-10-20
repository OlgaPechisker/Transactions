"""The composition root: settings in, working adapters out. Nothing here connects to a database."""

from pathlib import Path

import pytest

from bank_transactions import bootstrap
from bank_transactions.bootstrap import (
    DefaultComposition,
    build_identifier_protector,
    build_identity_resolver,
    build_import_service,
    build_parser_selector,
    build_workbook_reader,
)
from bank_transactions.config import ConfigurationError, Settings
from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import IdentityUnresolvedError
from bank_transactions.domain.models import CardHint, CardHintKey, CardIdentityHint
from bank_transactions.identity.models import CreateCard, IdentityIssue, IdentityQuestion
from bank_transactions.services.import_service import StatementImportService

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="  # base64 of 32 bytes
UNUSED_DSN = "postgresql+psycopg://user:pw@127.0.0.1:1/never_connected"


def example(prefix: str) -> Path:
    (path,) = [p for p in EXAMPLES.glob("*.xlsx") if p.name.startswith(prefix)]
    return path


@pytest.mark.parametrize(
    ("prefix", "issuer"),
    [
        ("9462", IssuerCode.ISRACARD),
        ("transaction-details_export_1785931743433", IssuerCode.MAX),
        ("transaction-details_export_1785938963329", IssuerCode.MAX),
        ("פירוט", IssuerCode.CAL),
    ],
)
def test_the_default_composition_detects_every_example_without_database_or_key(prefix, issuer):
    assert DefaultComposition().detect_issuer(Settings(), example(prefix)) is issuer


def test_a_workbook_that_is_not_a_statement_is_unrecognised(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "other.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "nothing to see"
    workbook.save(path)

    from bank_transactions.domain.errors import UnknownFormatError

    with pytest.raises(UnknownFormatError):
        DefaultComposition().detect_issuer(Settings(), path)


def test_the_reader_opens_an_example_workbook():
    workbook = build_workbook_reader().open(example("9462"))

    assert workbook.sheets


def test_parser_settings_reach_the_registry(monkeypatch):
    seen = {}
    real = bootstrap.build_default_registry

    def spy(**options):
        seen.update(options)
        return real(**options)

    monkeypatch.setattr(bootstrap, "build_default_registry", spy)
    settings = Settings(
        two_digit_years_back=30,
        fee_keywords=("fee", "עמלה"),
        fee_window_days=5,
        minimum_confidence=0.7,
    )

    build_parser_selector(settings)

    assert seen == {
        "two_digit_years_back": 30,
        "fee_keywords": ("fee", "עמלה"),
        "fee_window_days": 5,
        "minimum_confidence": 0.7,
    }


def test_unset_fee_keywords_leave_the_parser_defaults_in_place(monkeypatch):
    seen = {}
    real = bootstrap.build_default_registry
    monkeypatch.setattr(
        bootstrap, "build_default_registry", lambda **o: seen.update(o) or real(**o)
    )

    build_parser_selector(Settings())

    assert "fee_keywords" not in seen


def test_the_protector_binds_each_identifier_to_its_type():
    protector = build_identifier_protector(Settings(cipher_key=KEY))

    token = protector.encrypt(IdentifierType.CARD_LAST4, "4396")

    assert protector.decrypt(IdentifierType.CARD_LAST4, token) == "4396"
    with pytest.raises(Exception):  # noqa: B017 - any refusal to decrypt under another type
        protector.decrypt(IdentifierType.ACCOUNT_NUMBER, token)


def test_the_protector_needs_a_valid_key():
    with pytest.raises(ConfigurationError, match="BT_CIPHER_KEY"):
        build_identifier_protector(Settings())
    with pytest.raises(ConfigurationError, match="BT_CIPHER_KEY"):
        build_identifier_protector(Settings(cipher_key="AAAA"))


def question() -> IdentityQuestion:
    hint = CardIdentityHint(CardHint(CardHintKey(IssuerCode.MAX, "4396")))
    return IdentityQuestion(hint, IdentityIssue.NO_MATCHING_CARD)


def test_a_non_interactive_resolver_never_answers():
    resolver = build_identity_resolver(interactive=False, read=input, write=print)

    with pytest.raises(IdentityUnresolvedError):
        resolver.complete(question())


def test_an_interactive_resolver_prompts_through_the_given_streams():
    prompts, lines = [], []
    answers = iter(["Dana Levi", "", ""])

    def read(prompt):
        prompts.append(prompt)
        return next(answers)

    resolver = build_identity_resolver(interactive=True, read=read, write=lines.append)

    selection = resolver.complete(question())

    assert isinstance(selection, CreateCard)
    assert prompts and lines


def test_the_service_is_assembled_without_connecting_to_the_database():
    settings = Settings(dsn=UNUSED_DSN, cipher_key=KEY)

    service = build_import_service(settings, interactive=False)

    assert isinstance(service, StatementImportService)


def test_the_service_needs_both_the_dsn_and_the_key():
    with pytest.raises(ConfigurationError, match="BT_DSN"):
        build_import_service(Settings(cipher_key=KEY))
    with pytest.raises(ConfigurationError, match="BT_CIPHER_KEY"):
        build_import_service(Settings(dsn=UNUSED_DSN))


def test_initialising_the_database_needs_a_dsn_but_no_key():
    with pytest.raises(ConfigurationError, match="BT_DSN"):
        DefaultComposition().initialize_database(Settings(cipher_key=KEY))

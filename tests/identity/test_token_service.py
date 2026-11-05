import base64
import hashlib
import re

import pytest

from bank_transactions.domain.enums import IssuerCode, LoginKeyKind
from bank_transactions.identity.tokens import HmacTokenService
from bank_transactions.ports.token_service import TokenService

KEY = bytes(range(64))
OTHER_KEY = bytes(range(1, 65))
QUESTION_ID = re.compile(r"^iq_[a-z2-7]{26}$")


@pytest.fixture
def tokens():
    return HmacTokenService(KEY)


def test_service_is_a_token_service(tokens):
    assert isinstance(tokens, TokenService)


def test_an_empty_key_is_rejected():
    with pytest.raises(ValueError):
        HmacTokenService(b"")


def test_session_tokens_are_unique_url_safe_text_with_256_bits_of_randomness(tokens):
    first, second = tokens.new_session_token(), tokens.new_session_token()

    assert first != second
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first)
    assert len(base64.urlsafe_b64decode(first + "=")) == 32


def test_the_digest_of_a_token_is_its_sha256_regardless_of_key(tokens):
    assert tokens.digest("abc") == hashlib.sha256(b"abc").digest()
    assert tokens.digest("abc") == HmacTokenService(OTHER_KEY).digest("abc")


def test_csrf_is_derived_deterministically_from_the_session_digest(tokens):
    digest = tokens.digest(tokens.new_session_token())

    csrf = tokens.csrf_for(digest)

    assert csrf == tokens.csrf_for(digest)
    assert csrf == HmacTokenService(KEY).csrf_for(digest)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf)


def test_csrf_differs_per_session_and_per_key(tokens):
    first, second = tokens.digest("one"), tokens.digest("two")

    assert tokens.csrf_for(first) != tokens.csrf_for(second)
    assert tokens.csrf_for(first) != HmacTokenService(OTHER_KEY).csrf_for(first)


def test_question_ids_are_stable_for_the_same_key_issuer_and_hint(tokens):
    first = tokens.question_id(IssuerCode.MAX, "4396")

    assert first == tokens.question_id(IssuerCode.MAX, "4396")
    assert first == HmacTokenService(KEY).question_id(IssuerCode.MAX, "4396")


def test_question_ids_have_the_documented_shape_and_do_not_reveal_the_hint(tokens):
    question_id = tokens.question_id(IssuerCode.ISRACARD, "4396")

    assert QUESTION_ID.fullmatch(question_id)
    assert "4396" not in question_id


def test_question_ids_differ_by_key_issuer_and_hint(tokens):
    base = tokens.question_id(IssuerCode.MAX, "4396")

    assert base != HmacTokenService(OTHER_KEY).question_id(IssuerCode.MAX, "4396")
    assert base != tokens.question_id(IssuerCode.CAL, "4396")
    assert base != tokens.question_id(IssuerCode.MAX, "4397")


def test_throttle_keys_are_stable_32_byte_digests_per_kind_value_and_key(tokens):
    key = tokens.throttle_key(LoginKeyKind.EMAIL, "a@example.com")

    assert isinstance(key, bytes) and len(key) == 32
    assert key == tokens.throttle_key(LoginKeyKind.EMAIL, "a@example.com")
    assert key != tokens.throttle_key(LoginKeyKind.IP, "a@example.com")
    assert key != tokens.throttle_key(LoginKeyKind.EMAIL, "b@example.com")
    assert key != HmacTokenService(OTHER_KEY).throttle_key(LoginKeyKind.EMAIL, "a@example.com")
    assert b"example" not in key

import base64

import pytest

from bank_transactions.config import ConfigurationError, Settings

DSN = "postgresql+psycopg://app:s3cr3t-pw@127.0.0.1:5432/bank"


def key_text(length: int = 64) -> str:
    return base64.b64encode(bytes(range(length))).decode("ascii")


def test_defaults_match_the_documented_settings():
    settings = Settings.from_env({})

    assert settings.dsn is None
    assert settings.two_digit_years_back == 80
    assert settings.fee_keywords is None
    assert settings.fee_window_days == 3
    assert settings.minimum_confidence == 0.5


def test_every_setting_is_read_from_its_environment_variable():
    settings = Settings.from_env(
        {
            "BT_DSN": DSN,
            "BT_CIPHER_KEY": key_text(),
            "BT_TWO_DIGIT_YEARS_BACK": "60",
            "BT_FEE_KEYWORDS": "commission, fx fee ,,",
            "BT_FEE_WINDOW_DAYS": "5",
            "BT_MIN_CONFIDENCE": "0.75",
        }
    )

    assert settings.require_dsn() == DSN
    assert settings.require_cipher_key() == bytes(range(64))
    assert settings.two_digit_years_back == 60
    assert settings.fee_keywords == ("commission", "fx fee")
    assert settings.fee_window_days == 5
    assert settings.minimum_confidence == 0.75


def test_a_command_line_dsn_overrides_the_environment():
    settings = Settings.from_env({"BT_DSN": DSN}, dsn_override="postgresql+psycopg://other/db")

    assert settings.require_dsn() == "postgresql+psycopg://other/db"


def test_blank_optional_values_count_as_unset():
    settings = Settings.from_env({"BT_FEE_KEYWORDS": "  ", "BT_DSN": " "})

    assert settings.fee_keywords is None
    assert settings.dsn is None


@pytest.mark.parametrize("value", ["abc", "-1", "100", "7.5"])
def test_an_invalid_year_window_names_the_variable_not_the_value(value):
    with pytest.raises(ConfigurationError, match="BT_TWO_DIGIT_YEARS_BACK") as caught:
        Settings.from_env({"BT_TWO_DIGIT_YEARS_BACK": value})

    assert value not in str(caught.value)


@pytest.mark.parametrize("years_back", ["0", "99"])
def test_the_year_window_accepts_its_bounds(years_back):
    assert Settings.from_env({"BT_TWO_DIGIT_YEARS_BACK": years_back}).two_digit_years_back == int(
        years_back
    )


@pytest.mark.parametrize("value", ["abc", "-1"])
def test_an_invalid_fee_window_is_rejected(value):
    with pytest.raises(ConfigurationError, match="BT_FEE_WINDOW_DAYS"):
        Settings.from_env({"BT_FEE_WINDOW_DAYS": value})


@pytest.mark.parametrize("value", ["abc", "0", "1.5", "-0.1", "nan"])
def test_an_invalid_confidence_threshold_is_rejected(value):
    with pytest.raises(ConfigurationError, match="BT_MIN_CONFIDENCE"):
        Settings.from_env({"BT_MIN_CONFIDENCE": value})


def test_a_missing_dsn_is_reported_only_when_it_is_required():
    settings = Settings.from_env({})

    with pytest.raises(ConfigurationError, match="BT_DSN"):
        settings.require_dsn()


def test_a_missing_cipher_key_is_reported_only_when_it_is_required():
    settings = Settings.from_env({"BT_DSN": DSN})

    with pytest.raises(ConfigurationError, match="BT_CIPHER_KEY"):
        settings.require_cipher_key()


@pytest.mark.parametrize("length", [32, 48, 64])
def test_cipher_keys_of_the_supported_lengths_are_accepted(length):
    settings = Settings.from_env({"BT_CIPHER_KEY": key_text(length)})

    assert len(settings.require_cipher_key()) == length


@pytest.mark.parametrize("raw", ["not base64 !!", base64.b64encode(b"short").decode()])
def test_a_malformed_cipher_key_is_rejected_without_echoing_it(raw):
    settings = Settings.from_env({"BT_CIPHER_KEY": raw})

    with pytest.raises(ConfigurationError, match="BT_CIPHER_KEY") as caught:
        settings.require_cipher_key()

    assert raw not in str(caught.value)


def test_secrets_never_appear_in_the_settings_representation():
    settings = Settings.from_env({"BT_DSN": DSN, "BT_CIPHER_KEY": key_text()})

    text = repr(settings)

    assert "s3cr3t-pw" not in text
    assert key_text() not in text

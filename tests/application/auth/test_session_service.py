from datetime import timedelta

import pytest
from tests.fakes import FakeHasher

from bank_transactions.domain.enums import LoginKeyKind
from bank_transactions.domain.errors import (
    AuthenticationError,
    RateLimitedError,
    ValidationFailedError,
)
from bank_transactions.services.session_service import SESSION_LIFETIME, normalize_email

CLIENT_IP = "203.0.113.7"


class CountingHasher(FakeHasher):
    """Records every verification, so a test can see that an unknown email still costs one."""

    def __init__(self) -> None:
        self.verified: list[str] = []

    def verify(self, password_hash: str, password: str) -> bool:
        self.verified.append(password_hash)
        return super().verify(password_hash, password)


class OutdatedHashes(FakeHasher):
    """Every stored hash is outdated; new ones are tagged so the upgrade is visible."""

    def hash(self, password: str) -> str:
        return super().hash("upgraded:" + password)

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash in (self.PREFIX + password, self.PREFIX + "upgraded:" + password)

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith(self.PREFIX + "upgraded:")


def fail_sign_in(world, email: str, ip: str = CLIENT_IP, password: str = "wrong") -> None:
    with pytest.raises(AuthenticationError) as caught:
        world.service.sign_in(email, password, ip)
    assert caught.value.code == AuthenticationError.INVALID_CREDENTIALS


def test_signing_in_opens_a_session_that_expires_after_twelve_hours(world):
    user = world.add_user()

    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    assert issued.expires_at == world.clock.now() + timedelta(hours=12)
    assert issued.context.user_id == user.user_id
    assert issued.context.user_display_name == "Dana Levi"
    assert world.authenticator.authenticate(issued.token) == issued.context


def test_the_email_is_trimmed_and_case_insensitive(world):
    world.add_user("Dana@Example.com")

    issued = world.service.sign_in("  DANA@example.COM ", "s3cret", CLIENT_IP)

    assert issued.context.user_display_name == "Dana Levi"
    assert normalize_email(" A@B.c ") == "a@b.c"


def test_the_csrf_token_is_derived_from_the_session_token_digest(world):
    world.add_user()

    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    digest = world.tokens.digest(issued.token)
    assert issued.context.csrf_token == world.tokens.csrf_for(digest)
    assert world.authenticator.authenticate(issued.token).csrf_token == issued.context.csrf_token


def test_only_the_digest_of_the_token_is_stored(world):
    world.add_user()

    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    (stored,) = world.sessions.rows.values()
    assert stored.token_digest == world.tokens.digest(issued.token)
    assert issued.token.encode() not in stored.token_digest


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(world):
    world.add_user()

    with pytest.raises(AuthenticationError) as wrong_password:
        world.service.sign_in("dana@example.com", "nope", CLIENT_IP)
    with pytest.raises(AuthenticationError) as unknown_email:
        world.service.sign_in("nobody@example.com", "nope", CLIENT_IP)

    assert str(wrong_password.value) == str(unknown_email.value)
    assert wrong_password.value.code == unknown_email.value.code == "INVALID_CREDENTIALS"
    assert not world.sessions.rows


def test_an_unknown_email_still_costs_one_password_verification(make_world):
    hasher = CountingHasher()
    world = make_world(hasher)
    world.add_user()

    fail_sign_in(world, "nobody@example.com")

    assert len(hasher.verified) == 1
    assert hasher.verified[0].startswith(FakeHasher.PREFIX)
    assert hasher.verified[0] != world.users.find_by_email_key("dana@example.com").password_hash


def test_the_stored_hash_is_upgraded_when_it_is_outdated(make_world):
    world = make_world(OutdatedHashes())
    user = world.add_user()
    world.users.update_password_hash(user.user_id, "hashed:s3cret")

    world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    assert world.users.get(user.user_id).password_hash == "hashed:upgraded:s3cret"
    assert world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP).token


def test_the_eleventh_attempt_from_one_address_is_rate_limited(world):
    for number in range(10):
        world.add_user(f"user{number}@example.com")
        fail_sign_in(world, f"user{number}@example.com")
    world.add_user("late@example.com")

    with pytest.raises(RateLimitedError) as limited:
        world.service.sign_in("late@example.com", "s3cret", CLIENT_IP)

    assert limited.value.retry_after_s == 15 * 60


def test_the_eleventh_attempt_on_one_email_is_rate_limited_from_any_address(world):
    world.add_user()
    for number in range(10):
        fail_sign_in(world, "dana@example.com", ip=f"198.51.100.{number}")

    with pytest.raises(RateLimitedError):
        world.service.sign_in("dana@example.com", "s3cret", "192.0.2.99")


def test_ten_failures_are_still_answered_as_bad_credentials(world):
    world.add_user()

    for _ in range(10):
        fail_sign_in(world, "dana@example.com")


def test_failures_are_forgotten_after_fifteen_minutes(world):
    world.add_user()
    for _ in range(10):
        fail_sign_in(world, "dana@example.com")

    world.clock.advance(timedelta(minutes=15, seconds=1))

    assert world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP).token


def test_old_failures_are_purged_when_a_new_one_is_recorded(world):
    world.add_user()
    for _ in range(3):
        fail_sign_in(world, "dana@example.com")
    world.clock.advance(timedelta(minutes=16))

    fail_sign_in(world, "dana@example.com")

    assert len(world.throttle.failures) == 2


def test_failures_of_another_email_and_address_do_not_count(world):
    world.add_user()
    for number in range(10):
        fail_sign_in(world, f"other{number}@example.com", ip=f"198.51.100.{number}")

    assert world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP).token


def test_the_throttle_stores_digests_not_the_email_or_address(world):
    world.add_user()

    fail_sign_in(world, " Dana@Example.com ")

    keys = {failure.key_digest for failure in world.throttle.failures}
    assert keys == {
        world.tokens.throttle_key(LoginKeyKind.EMAIL, "dana@example.com"),
        world.tokens.throttle_key(LoginKeyKind.IP, CLIENT_IP),
    }
    assert len(world.throttle.failures) == 2


def test_signing_out_ends_the_session(world):
    world.add_user()
    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    world.service.sign_out(issued.context)

    assert world.authenticator.authenticate(issued.token) is None


def test_signing_out_twice_is_harmless(world):
    world.add_user()
    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    world.service.sign_out(issued.context)
    world.clock.advance(timedelta(minutes=5))
    world.service.sign_out(issued.context)

    (stored,) = world.sessions.rows.values()
    assert stored.revoked_at == world.clock.now() - timedelta(minutes=5)


def test_a_session_stops_working_after_twelve_hours(world):
    world.add_user()
    issued = world.service.sign_in("dana@example.com", "s3cret", CLIENT_IP)

    world.clock.advance(SESSION_LIFETIME - timedelta(seconds=1))
    assert world.authenticator.authenticate(issued.token) is not None
    world.clock.advance(timedelta(seconds=1))

    assert world.authenticator.authenticate(issued.token) is None


def test_an_unknown_token_is_not_authenticated(world):
    assert world.authenticator.authenticate("never-issued") is None


def test_create_user_stores_a_hash_and_a_normalised_key(world):
    user = world.service.create_user("  Dana@Example.com ", " Dana Levi ", "s3cret")

    assert user.email == "Dana@Example.com"
    assert user.display_name == "Dana Levi"
    assert user.password_hash == "hashed:s3cret"
    assert world.users.find_by_email_key("dana@example.com") == user
    assert world.service.sign_in("DANA@example.com", "s3cret", CLIENT_IP).token


@pytest.mark.parametrize(
    ("email", "name", "password", "field"),
    [
        ("not-an-email", "Dana", "pw", "email"),
        ("@example.com", "Dana", "pw", "email"),
        ("dana@", "Dana", "pw", "email"),
        ("dana@example.com", "   ", "pw", "name"),
        ("dana@example.com", "Dana", "", "password"),
    ],
)
def test_create_user_rejects_an_unusable_value(world, email, name, password, field):
    with pytest.raises(ValidationFailedError) as caught:
        world.service.create_user(email, name, password)

    assert [error.field for error in caught.value.errors] == [field]
    assert not world.users.rows


def test_create_user_rejects_a_duplicate_email_in_any_case(world):
    world.add_user("dana@example.com")

    with pytest.raises(ValidationFailedError) as caught:
        world.service.create_user(" DANA@example.com", "Other", "pw")

    assert caught.value.errors[0].code == "ALREADY_EXISTS"
    assert len(world.users.rows) == 1

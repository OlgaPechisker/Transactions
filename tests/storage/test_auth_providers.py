"""Sessions, sign-in failure counters and the cards a user sees, on PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest

from bank_transactions.domain.enums import LoginKeyKind
from bank_transactions.persistence.commands import NewLoginFailure, NewSession

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
DIGEST = b"d" * 32


def open_session(factory, user, digest=DIGEST, expires_at=NOW + timedelta(hours=12)):
    with factory.create() as uow:
        record = uow.sessions.create(NewSession(user.user_id, digest, NOW, expires_at))
        uow.commit()
    return record


def test_an_open_session_is_found_by_its_digest(factory, seed_user):
    user = seed_user()

    created = open_session(factory, user)

    with factory.create() as uow:
        found = uow.sessions.find_active_by_digest(DIGEST, NOW + timedelta(hours=1))
        unknown = uow.sessions.find_active_by_digest(b"x" * 32, NOW)
    assert found == created
    assert (found.user_id, found.token_digest, found.revoked_at) == (user.user_id, DIGEST, None)
    assert unknown is None


def test_a_session_is_not_found_once_it_has_expired(factory, seed_user):
    open_session(factory, seed_user())

    with factory.create() as uow:
        assert uow.sessions.find_active_by_digest(DIGEST, NOW + timedelta(hours=12)) is None
        just_before = NOW + timedelta(hours=12) - timedelta(seconds=1)
        assert uow.sessions.find_active_by_digest(DIGEST, just_before) is not None


def test_a_revoked_session_is_not_found_and_revoking_again_keeps_the_first_time(
    factory, seed_user, sql
):
    session = open_session(factory, seed_user())
    first, second = NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)

    for moment in (first, second):
        with factory.create() as uow:
            uow.sessions.revoke(session.session_id, moment)
            uow.commit()

    with factory.create() as uow:
        assert uow.sessions.find_active_by_digest(DIGEST, NOW) is None
    assert sql("SELECT revoked_at FROM sessions")[0][0] == first


def test_failures_are_counted_per_kind_and_key_within_the_window(factory):
    since = NOW - timedelta(minutes=15)
    with factory.create() as uow:
        for moment in (NOW - timedelta(minutes=20), NOW - timedelta(minutes=5), NOW):
            uow.login_throttle.record_failure(
                NewLoginFailure(LoginKeyKind.EMAIL, b"e" * 32, moment)
            )
        uow.login_throttle.record_failure(NewLoginFailure(LoginKeyKind.IP, b"e" * 32, NOW))
        uow.login_throttle.record_failure(NewLoginFailure(LoginKeyKind.EMAIL, b"f" * 32, NOW))
        uow.commit()

    with factory.create() as uow:
        counts = uow.login_throttle
        assert counts.count_failures(LoginKeyKind.EMAIL, b"e" * 32, since) == 2
        assert counts.count_failures(LoginKeyKind.IP, b"e" * 32, since) == 1
        assert counts.count_failures(LoginKeyKind.EMAIL, b"z" * 32, since) == 0


def test_purging_removes_only_failures_older_than_the_cutoff(factory):
    with factory.create() as uow:
        for minutes in (30, 20, 1):
            moment = NOW - timedelta(minutes=minutes)
            uow.login_throttle.record_failure(NewLoginFailure(LoginKeyKind.IP, b"i" * 32, moment))
        uow.commit()

    with factory.create() as uow:
        removed = uow.login_throttle.purge_before(NOW - timedelta(minutes=15))
        uow.commit()

    assert removed == 2
    with factory.create() as uow:
        assert (
            uow.login_throttle.count_failures(LoginKeyKind.IP, b"i" * 32, NOW - timedelta(days=1))
            == 1
        )


def test_a_user_sees_the_cards_they_claimed_with_the_owner_name(
    factory, seed_user, seed_claimed_card
):
    user = seed_user()
    seeded = seed_claimed_card(user, last4="4580", owner_name="Noa Levi")

    with factory.create() as uow:
        (card,) = uow.visible_cards.list_visible(user.user_id)
        found = uow.visible_cards.get_visible(user.user_id, card.public_id)

    assert found == card
    assert card.card_id == seeded.card_id
    assert (card.nickname, card.owner_display_name) == (None, "Noa Levi")


def test_cards_are_listed_in_creation_order(factory, seed_user, seed_claimed_card):
    user = seed_user()
    first = seed_claimed_card(user, last4="1111")
    for last4 in ("2222", "3333"):
        seed_claimed_card(user, last4=last4, same_account_as=first)

    with factory.create() as uow:
        cards = uow.visible_cards.list_visible(user.user_id)

    assert [c.card_id for c in cards] == sorted(c.card_id for c in cards)
    assert len(cards) == 3


def test_a_user_cannot_see_or_rename_another_users_card(factory, seed_user, seed_claimed_card):
    owner = seed_user("owner@example.com", "Owner")
    stranger = seed_user("stranger@example.com", "Stranger")
    seed_claimed_card(owner, last4="4580")
    with factory.create() as uow:
        (card,) = uow.visible_cards.list_visible(owner.user_id)

    with factory.create() as uow:
        assert uow.visible_cards.list_visible(stranger.user_id) == ()
        assert uow.visible_cards.get_visible(stranger.user_id, card.public_id) is None
        assert uow.visible_cards.set_display_name(stranger.user_id, card.public_id, "mine") is None
        uow.commit()

    with factory.create() as uow:
        assert uow.visible_cards.get_visible(owner.user_id, card.public_id).nickname is None


def test_renaming_a_card_returns_it_and_none_clears_the_name(factory, seed_user, seed_claimed_card):
    user = seed_user()
    seed_claimed_card(user)
    with factory.create() as uow:
        (card,) = uow.visible_cards.list_visible(user.user_id)

    with factory.create() as uow:
        renamed = uow.visible_cards.set_display_name(user.user_id, card.public_id, "Travel")
        uow.commit()
    with factory.create() as uow:
        assert uow.visible_cards.get_visible(user.user_id, card.public_id).nickname == "Travel"
        cleared = uow.visible_cards.set_display_name(user.user_id, card.public_id, None)
        uow.commit()

    assert renamed.nickname == "Travel"
    assert renamed.public_id == card.public_id
    assert cleared.nickname is None

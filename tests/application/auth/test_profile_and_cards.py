import pytest

from bank_transactions.domain.errors import NotFoundError, ValidationFailedError
from bank_transactions.domain.ids import PublicId, UserId


def test_me_shows_the_profile_and_the_pending_currency_count(world):
    user = world.add_user()
    world.review_counter.counts[user.user_id] = 3
    context = world.service.sign_in("dana@example.com", "s3cret", "203.0.113.7").context

    me = world.profile.me(context)

    assert (me.public_id, me.name, me.email) == (user.public_id, "Dana Levi", "dana@example.com")
    assert me.csrf_token == context.csrf_token
    assert me.pending_currency_validations == 3


def test_me_counts_only_the_callers_pending_validations(world):
    world.add_user("a@example.com")
    other = world.add_user("b@example.com", "Other")
    world.review_counter.counts[other.user_id] = 9
    context = world.service.sign_in("a@example.com", "s3cret", "203.0.113.7").context

    assert world.profile.me(context).pending_currency_validations == 0


def signed_in(world, email="dana@example.com"):
    world.add_user(email)
    return world.service.sign_in(email, "s3cret", "203.0.113.7").context


def test_the_card_list_shows_label_last4_and_the_owner_falls_back_to_the_user(world):
    context = signed_in(world)
    world.visible_cards.add(context.user_id, "crd_1", nickname="Travel")

    (card,) = world.cards.list(context)

    assert (card.public_id, card.last4, card.nickname) == ("crd_1", "4580", "Travel")
    assert card.owner_name == "Dana Levi"
    assert card.label.endswith("4580")


def test_a_user_with_no_cards_gets_an_empty_list(world):
    assert world.cards.list(signed_in(world)) == ()


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("  Work card  ", "Work card"), ("Work", "Work"), ("   ", None), ("", None), (None, None)],
)
def test_a_nickname_is_trimmed_and_a_blank_one_clears_it(world, typed, stored):
    context = signed_in(world)
    world.visible_cards.add(context.user_id, "crd_1", nickname="Old")

    card = world.cards.set_nickname(context, PublicId("crd_1"), typed)

    assert card.nickname == stored
    assert world.cards.list(context)[0].nickname == stored


def test_a_nickname_of_120_characters_after_trimming_is_accepted(world):
    context = signed_in(world)
    world.visible_cards.add(context.user_id, "crd_1")

    card = world.cards.set_nickname(context, PublicId("crd_1"), "  " + "n" * 120 + "  ")

    assert card.nickname == "n" * 120


def test_a_nickname_over_120_characters_is_rejected_and_nothing_changes(world):
    context = signed_in(world)
    world.visible_cards.add(context.user_id, "crd_1", nickname="Old")

    with pytest.raises(ValidationFailedError) as caught:
        world.cards.set_nickname(context, PublicId("crd_1"), "n" * 121)

    assert [(e.field, e.code) for e in caught.value.errors] == [("nickname", "TOO_LONG")]
    assert world.cards.list(context)[0].nickname == "Old"


def test_another_users_card_and_an_unknown_card_are_not_found(world):
    context = signed_in(world, "a@example.com")
    world.add_user("b@example.com")
    world.visible_cards.add(UserId(2), "crd_b", nickname="B only")

    with pytest.raises(NotFoundError):
        world.cards.set_nickname(context, PublicId("crd_b"), "mine now")
    with pytest.raises(NotFoundError):
        world.cards.set_nickname(context, PublicId("crd_missing"), "x")

    assert world.cards.list(context) == ()
    assert world.visible_cards.cards[2][0].nickname == "B only"

import pytest

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.errors import PersistenceError
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner

pytestmark = pytest.mark.postgres


def owner_count(sql) -> int:
    return sql("select count(*) from owners")[0][0]


def test_commit_makes_work_visible_to_everyone(factory, sql):
    with factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        uow.commit()

    assert owner_count(sql) == 1


def test_leaving_the_context_without_commit_rolls_back(factory, sql):
    with factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))

    assert owner_count(sql) == 0


def test_an_exception_inside_the_context_rolls_back_and_propagates(factory, sql):
    with pytest.raises(RuntimeError, match="boom"), factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        uow.commit()
        uow.owners.create(NewOwner("Avi", OwnerMatchKey("avi")))
        raise RuntimeError("boom")

    assert owner_count(sql) == 1


def test_rollback_after_commit_is_a_no_op(factory, sql):
    with factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        uow.commit()
        uow.rollback()

    assert owner_count(sql) == 1


def test_an_explicit_rollback_discards_flushed_work(factory, sql):
    with factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        uow.rollback()

    assert owner_count(sql) == 0


def test_every_created_unit_of_work_is_independent(factory, sql):
    with factory.create() as first, factory.create() as second:
        first.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))

        assert second.owners.list_all() == ()
        first.commit()

    assert owner_count(sql) == 1


def test_the_connection_is_released_when_the_context_exits(factory, engine):
    for _ in range(3):
        with factory.create() as uow:
            uow.owners.list_all()

    assert engine.pool.checkedout() == 0


def test_the_connection_is_released_after_a_failed_statement(factory, engine):
    with pytest.raises(PersistenceError), factory.create() as uow:
        uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        uow.owners.create(NewOwner("Dana 2", OwnerMatchKey("dana")))

    assert engine.pool.checkedout() == 0


def test_identity_gate_and_domain_writes_share_one_dry_run_rollback(
    factory, cipher, new_aggregate, sql, start_run
):
    run_id = start_run()

    with factory.create() as uow:
        owner = uow.owners.create(NewOwner("Dana", OwnerMatchKey("dana")))
        account = uow.accounts.create(
            NewAccount(
                owner.owner_id, "LEUMI", cipher.encrypt(IdentifierType.ACCOUNT_NUMBER, "123"), None
            )
        )
        card = uow.cards.create(
            NewCard(
                owner.owner_id,
                account.account_id,
                IssuerCode.MAX,
                cipher.encrypt(IdentifierType.CARD_LAST4, "4396"),
                None,
            )
        )
        uow.transactions.insert_many([new_aggregate(card.card_id, run_id)])
        uow.rollback()

    for table in ("owners", "accounts", "cards", "transactions"):
        assert sql(f"select count(*) from {table}")[0][0] == 0
    assert sql("select count(*) from import_runs")[0][0] == 1

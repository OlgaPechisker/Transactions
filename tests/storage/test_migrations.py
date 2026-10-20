import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from bank_transactions.persistence.db import downgrade_database, upgrade_database
from bank_transactions.persistence.orm.models import Base

pytestmark = pytest.mark.postgres

DOMAIN_TABLES = {
    "owners",
    "accounts",
    "cards",
    "import_runs",
    "installment_plans",
    "transactions",
    "transaction_review_reasons",
    "foreign_transaction_details",
}

ENUMS = {
    "transaction_type_enum": ["REGULAR", "INSTALLMENTS", "RECURRING", "IMMEDIATE_CHARGE"],
    "channel_enum": ["INTERNET", "PHONE", "MOBILE", "UNKNOWN"],
    "review_reason_enum": ["UNKNOWN_CURRENCY", "UNMATCHED_FOREIGN_FEE", "AMBIGUOUS_INSTALLMENT"],
    "import_status_enum": ["RUNNING", "SUCCESS", "PARTIAL", "NEEDS_INPUT", "FAILED"],
}


@pytest.fixture
def tables(sql):
    def list_tables():
        rows = sql(
            "select table_name from information_schema.tables where table_schema = current_schema()"
        )
        return {row[0] for row in rows}

    return list_tables


@pytest.fixture
def enum_labels(sql):
    def read():
        rows = sql(
            """
            select t.typname, array_agg(e.enumlabel::text order by e.enumsortorder)
            from pg_type t
            join pg_enum e on e.enumtypid = t.oid
            where t.typnamespace = current_schema()::regnamespace
            group by t.typname
            """
        )
        return {name: labels for name, labels in rows}

    return read


@pytest.fixture
def index_definitions(sql):
    rows = sql("select indexname, indexdef from pg_indexes where schemaname = current_schema()")
    return {name: definition for name, definition in rows}


@pytest.fixture
def constraint_definitions(sql):
    rows = sql(
        """
        select conname, pg_get_constraintdef(oid)
        from pg_constraint where connamespace = current_schema()::regnamespace
        """
    )
    return {name: definition for name, definition in rows}


@pytest.fixture
def columns(sql):
    rows = sql(
        """
        select table_name, column_name, data_type, numeric_precision, numeric_scale,
               character_maximum_length, is_nullable
        from information_schema.columns where table_schema = current_schema()
        """
    )
    return {(row[0], row[1]): row[2:] for row in rows}


def test_upgrade_creates_every_table_of_the_plan(tables):
    assert tables() >= DOMAIN_TABLES


def test_upgrade_creates_the_four_enum_types_with_their_exact_values(enum_labels):
    assert enum_labels() == ENUMS


def test_upgrade_leaves_the_public_schema_untouched(sql):
    public_tables = {
        name
        for (name,) in sql(
            "select table_name from information_schema.tables where table_schema = 'public'"
        )
    }

    assert public_tables.isdisjoint(DOMAIN_TABLES)


def test_card_search_indexes_are_non_unique(index_definitions):
    issuer_last4 = index_definitions["ix_cards_issuer_last4"]
    account_issuer_last4 = index_definitions["ix_cards_account_issuer_last4"]

    assert "UNIQUE" not in issuer_last4
    assert "(issuer_code, last4_enc)" in issuer_last4
    assert "UNIQUE" not in account_issuer_last4
    assert "(account_id, issuer_code, last4_enc)" in account_issuer_last4


def test_there_is_no_unique_constraint_on_card_last4(constraint_definitions, index_definitions):
    card_uniques = [
        definition
        for definition in [*constraint_definitions.values(), *index_definitions.values()]
        if "UNIQUE" in definition and "last4_enc" in definition
    ]

    assert card_uniques == []


def test_reference_index_includes_installment_number_null_safely(index_definitions):
    definition = index_definitions["ux_transactions_issuer_reference"]

    assert "UNIQUE INDEX" in definition
    assert "(card_id, transaction_date, issuer_reference, installment_number)" in definition
    assert "NULLS NOT DISTINCT" in definition
    assert "WHERE (issuer_reference IS NOT NULL)" in definition


def test_installment_plan_reference_index_is_partial_and_unique(index_definitions):
    definition = index_definitions["ux_installment_plans_issuer_reference"]

    assert "UNIQUE INDEX" in definition
    assert "(card_id, issuer_reference)" in definition
    assert "WHERE (issuer_reference IS NOT NULL)" in definition


def test_installment_plan_natural_key_is_unconditionally_unique(constraint_definitions):
    definition = constraint_definitions["uq_installment_plans_natural"]

    assert definition == (
        "UNIQUE (card_id, merchant_name_norm, purchase_date, total_amount, currency, "
        "total_installments)"
    )


def test_account_ciphertext_pair_is_unique(constraint_definitions):
    assert constraint_definitions["uq_accounts_bank_code_account_number_enc"] == (
        "UNIQUE (bank_code, account_number_enc)"
    )


def test_a_composite_foreign_key_ties_the_card_owner_to_the_account_owner(constraint_definitions):
    definition = constraint_definitions["fk_cards_account_owner"]

    assert definition.startswith(
        "FOREIGN KEY (account_id, owner_id) REFERENCES accounts(id, owner_id)"
    )


@pytest.mark.parametrize(
    "index",
    [
        "ix_transactions_card_charge_date",
        "ix_transactions_card_transaction_date",
        "ix_transactions_merchant_name_norm",
        "ix_transactions_bank_category_raw",
        "ix_transactions_installment_plan_id",
        "ix_transaction_review_reasons_reason",
    ],
)
def test_analysis_indexes_exist(index_definitions, index):
    assert index in index_definitions


def test_card_ownership_and_account_links_are_mandatory(columns):
    assert columns[("cards", "owner_id")][-1] == "NO"
    assert columns[("cards", "account_id")][-1] == "NO"
    assert columns[("accounts", "owner_id")][-1] == "NO"
    assert columns[("cards", "last4_enc")][0] == "bytea"
    assert columns[("cards", "last4_enc")][-1] == "NO"


def test_money_rate_currency_and_timestamp_column_types(columns):
    assert columns[("transactions", "billing_amount")][:3] == ("numeric", 14, 2)
    assert columns[("transactions", "original_amount")][-1] == "YES"
    assert columns[("installment_plans", "total_amount")][:3] == ("numeric", 14, 2)
    assert columns[("foreign_transaction_details", "commission_amount")][:3] == ("numeric", 14, 2)
    assert columns[("foreign_transaction_details", "stated_fx_rate")][:3] == ("numeric", 18, 6)
    assert columns[("foreign_transaction_details", "stated_fx_rate")][-1] == "YES"
    assert columns[("foreign_transaction_details", "effective_fx_rate")][-1] == "NO"
    assert columns[("transactions", "billing_currency")][0] == "character"
    assert columns[("transactions", "billing_currency")][3] == 3
    assert columns[("installment_plans", "currency")][-1] == "NO"
    assert columns[("transactions", "charge_date")][-1] == "NO"
    assert columns[("import_runs", "started_at")][0] == "timestamp with time zone"
    assert columns[("import_runs", "issuer_code")][-1] == "YES"
    assert columns[("import_runs", "finished_at")][-1] == "YES"


def test_downgrade_removes_every_table_and_enum_type(
    postgres_dsn, migrated_schema, tables, enum_labels
):
    downgrade_database(postgres_dsn, schema=migrated_schema)

    assert tables() - {"alembic_version"} == set()
    assert enum_labels() == {}


def test_the_schema_can_be_upgraded_downgraded_and_upgraded_again(
    postgres_dsn, migrated_schema, tables, enum_labels
):
    downgrade_database(postgres_dsn, schema=migrated_schema)
    upgrade_database(postgres_dsn, schema=migrated_schema)

    assert tables() >= DOMAIN_TABLES
    assert enum_labels() == ENUMS


def test_upgrading_an_already_current_schema_changes_nothing(
    postgres_dsn, migrated_schema, sql, tables
):
    before = tables()

    upgrade_database(postgres_dsn, schema=migrated_schema)

    assert tables() == before
    assert len(sql("select version_num from alembic_version")) == 1


def test_migrated_schema_matches_the_orm_metadata(engine):
    with engine.connect() as connection:
        differences = compare_metadata(
            MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
        )

    assert differences == []

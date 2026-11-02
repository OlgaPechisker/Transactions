"""Initial schema: users, sessions, claims, owners, accounts, cards, import runs, installment plans,
transactions, categorisation, trips and currency validation.

Revision ID: 0001
Revises:

The schema is created for an empty database; there is no backfill from an earlier layout.
The downgrade is DESTRUCTIVE: it drops every table and enum type, and with them all user data.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

CURRENCY_FORMAT = "~ '^[A-Z]{3}$'"
ISSUERS = "'ISRACARD', 'MAX', 'CAL'"

ENUM_LABELS = {
    "transaction_type_enum": ("REGULAR", "INSTALLMENTS", "RECURRING", "IMMEDIATE_CHARGE"),
    "channel_enum": ("INTERNET", "PHONE", "MOBILE", "UNKNOWN"),
    "review_reason_enum": ("UNKNOWN_CURRENCY", "UNMATCHED_FOREIGN_FEE", "AMBIGUOUS_INSTALLMENT"),
    "import_status_enum": ("RUNNING", "SUCCESS", "PARTIAL", "NEEDS_INPUT", "FAILED"),
    "login_key_kind_enum": ("EMAIL", "IP"),
}


def enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUM_LABELS[name], name=name, create_type=False)


def identity_pk() -> sa.Column[int]:
    return sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True)


def bigint_pk() -> sa.Column[int]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True)


def created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def timestamp(name: str, nullable: bool = False) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUM_LABELS:
        enum(name).create(bind, checkfirst=False)

    op.create_table(
        "users",
        bigint_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        created_at(),
        sa.UniqueConstraint("public_id", name="uq_users_public_id"),
        sa.UniqueConstraint("email_key", name="uq_users_email_key"),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_users_display_name"),
        sa.CheckConstraint("email_key = lower(btrim(email_key))", name="ck_users_email_key"),
    )

    op.create_table(
        "sessions",
        bigint_pk(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_digest", postgresql.BYTEA(), nullable=False),
        timestamp("created_at"),
        timestamp("expires_at"),
        timestamp("revoked_at", nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_digest", name="uq_sessions_token_digest"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "login_failures",
        bigint_pk(),
        sa.Column("key_kind", enum("login_key_kind_enum"), nullable=False),
        sa.Column("key_digest", postgresql.BYTEA(), nullable=False),
        timestamp("failed_at"),
    )
    op.create_index(
        "ix_login_failures_key_time", "login_failures", ["key_kind", "key_digest", "failed_at"]
    )

    op.create_table(
        "card_ownership_claims",
        bigint_pk(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("issuer_code", sa.String(16), nullable=False),
        sa.Column("last4_enc", postgresql.BYTEA(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_card_ownership_claims_user_id", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("issuer_code", "last4_enc", name="uq_claims_issuer_last4"),
        sa.UniqueConstraint("id", "issuer_code", "last4_enc", "user_id", name="uq_claims_key_user"),
        sa.CheckConstraint(
            f"issuer_code IN ({ISSUERS})", name="ck_card_ownership_claims_issuer_code"
        ),
    )
    op.create_index("ix_claims_user_id", "card_ownership_claims", ["user_id"])

    op.create_table(
        "owners",
        identity_pk(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("match_key", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_owners_user_id", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("user_id", "match_key", name="uq_owners_user_match_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_owners_id_user"),
        sa.CheckConstraint(
            "(display_name IS NULL) = (match_key IS NULL)", name="ck_owners_placeholder"
        ),
    )
    op.create_index(
        "ux_owners_user_placeholder",
        "owners",
        ["user_id"],
        unique=True,
        postgresql_where=text("match_key IS NULL"),
    )

    op.create_table(
        "accounts",
        identity_pk(),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_code", sa.Text(), nullable=True),
        sa.Column("account_number_enc", postgresql.BYTEA(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["owner_id", "user_id"], ["owners.id", "owners.user_id"], name="fk_accounts_owner_user"
        ),
        sa.UniqueConstraint(
            "user_id", "bank_code", "account_number_enc", name="uq_accounts_user_bank_number"
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_accounts_id_owner_id"),
        sa.UniqueConstraint("id", "owner_id", "user_id", name="uq_accounts_id_owner_user"),
    )
    op.create_index(
        "ux_accounts_owner_placeholder",
        "accounts",
        ["owner_id"],
        unique=True,
        postgresql_where=text("bank_code IS NULL AND account_number_enc IS NULL"),
    )

    op.create_table(
        "cards",
        identity_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("claim_id", sa.BigInteger(), nullable=False),
        sa.Column("issuer_code", sa.String(16), nullable=False),
        sa.Column("last4_enc", postgresql.BYTEA(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        created_at(),
        sa.UniqueConstraint("public_id", name="uq_cards_public_id"),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], name="fk_cards_owner_id"),
        sa.ForeignKeyConstraint(
            ["account_id", "owner_id", "user_id"],
            ["accounts.id", "accounts.owner_id", "accounts.user_id"],
            name="fk_cards_account_owner",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "issuer_code", "last4_enc", "user_id"],
            [
                "card_ownership_claims.id",
                "card_ownership_claims.issuer_code",
                "card_ownership_claims.last4_enc",
                "card_ownership_claims.user_id",
            ],
            name="fk_cards_claim",
        ),
        sa.CheckConstraint(f"issuer_code IN ({ISSUERS})", name="ck_cards_issuer_code"),
    )
    op.create_index("ix_cards_issuer_last4", "cards", ["issuer_code", "last4_enc"])
    op.create_index(
        "ix_cards_account_issuer_last4", "cards", ["account_id", "issuer_code", "last4_enc"]
    )
    op.create_index("ix_cards_claim_id", "cards", ["claim_id"])

    op.create_table(
        "import_runs",
        identity_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("issuer_code", sa.String(16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", enum("import_status_enum"), nullable=False),
        sa.Column("rows_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_duplicate", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("public_id", name="uq_import_runs_public_id"),
        sa.CheckConstraint(
            f"issuer_code IS NULL OR issuer_code IN ({ISSUERS})", name="ck_import_runs_issuer_code"
        ),
        sa.CheckConstraint(
            "rows_total >= 0 AND rows_imported >= 0 AND rows_duplicate >= 0 AND rows_skipped >= 0",
            name="ck_import_runs_counts_non_negative",
        ),
    )
    op.create_index("ix_import_runs_status_started_at", "import_runs", ["status", "started_at"])
    op.create_index("ix_import_runs_file_sha256", "import_runs", ["file_sha256"])
    op.create_index(
        "ux_import_runs_running_sha",
        "import_runs",
        ["file_sha256"],
        unique=True,
        postgresql_where=text("status = 'RUNNING'"),
    )

    op.create_table(
        "installment_plans",
        identity_pk(),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("issuer_reference", sa.Text(), nullable=True),
        sa.Column("merchant_name_norm", sa.Text(), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("total_installments", sa.SmallInteger(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], name="fk_installment_plans_card_id"),
        sa.UniqueConstraint(
            "card_id",
            "merchant_name_norm",
            "purchase_date",
            "total_amount",
            "currency",
            "total_installments",
            name="uq_installment_plans_natural",
        ),
        sa.CheckConstraint("total_installments >= 1", name="ck_installment_plans_installments"),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_installment_plans_currency"),
    )
    op.create_index(
        "ux_installment_plans_issuer_reference",
        "installment_plans",
        ["card_id", "issuer_reference"],
        unique=True,
        postgresql_where=text("issuer_reference IS NOT NULL"),
    )

    op.create_table(
        "transactions",
        identity_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), nullable=False),
        sa.Column("issuer_reference", sa.Text(), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("charge_date", sa.Date(), nullable=False),
        sa.Column("billing_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("billing_currency", sa.CHAR(3), nullable=True),
        sa.Column("original_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("original_currency", sa.CHAR(3), nullable=True),
        sa.Column("source_original_currency", sa.CHAR(3), nullable=True),
        sa.Column("merchant_name_raw", sa.Text(), nullable=False),
        sa.Column("merchant_name_norm", sa.Text(), nullable=False),
        sa.Column("merchant_city", sa.Text(), nullable=True),
        sa.Column("merchant_country", sa.Text(), nullable=True),
        sa.Column("bank_category_raw", sa.Text(), nullable=True),
        sa.Column("transaction_type", enum("transaction_type_enum"), nullable=False),
        sa.Column("channel", enum("channel_enum"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("installment_plan_id", sa.Integer(), nullable=True),
        sa.Column("installment_number", sa.Integer(), nullable=True),
        created_at(),
        sa.UniqueConstraint("public_id", name="uq_transactions_public_id"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], name="fk_transactions_card_id"),
        sa.ForeignKeyConstraint(
            ["import_run_id"], ["import_runs.id"], name="fk_transactions_import_run_id"
        ),
        sa.ForeignKeyConstraint(
            ["installment_plan_id"],
            ["installment_plans.id"],
            name="fk_transactions_installment_plan_id",
        ),
        sa.CheckConstraint(
            "installment_number IS NULL OR installment_number >= 1",
            name="ck_transactions_installment_number",
        ),
        sa.CheckConstraint(
            f"billing_currency IS NULL OR billing_currency {CURRENCY_FORMAT}",
            name="ck_transactions_billing_currency",
        ),
        sa.CheckConstraint(
            f"original_currency IS NULL OR original_currency {CURRENCY_FORMAT}",
            name="ck_transactions_original_currency",
        ),
        sa.CheckConstraint(
            f"source_original_currency IS NULL OR source_original_currency {CURRENCY_FORMAT}",
            name="ck_transactions_source_original_currency",
        ),
    )
    # installment_number is part of the key so installment charges sharing a voucher stay
    # distinct, and NULLS NOT DISTINCT keeps the regular (non-installment) key unique too.
    op.create_index(
        "ux_transactions_issuer_reference",
        "transactions",
        ["card_id", "transaction_date", "issuer_reference", "installment_number"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=text("issuer_reference IS NOT NULL"),
    )
    op.create_index("ix_transactions_card_charge_date", "transactions", ["card_id", "charge_date"])
    op.create_index(
        "ix_transactions_card_transaction_date", "transactions", ["card_id", "transaction_date"]
    )
    op.create_index("ix_transactions_merchant_name_norm", "transactions", ["merchant_name_norm"])
    op.create_index("ix_transactions_bank_category_raw", "transactions", ["bank_category_raw"])
    op.create_index("ix_transactions_installment_plan_id", "transactions", ["installment_plan_id"])
    op.create_index("ix_transactions_import_run_id", "transactions", ["import_run_id"])
    op.create_index(
        "ix_transactions_card_original_currency_date",
        "transactions",
        ["card_id", "original_currency", "transaction_date"],
    )

    op.create_table(
        "transaction_review_reasons",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("reason", enum("review_reason_enum"), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("transaction_id", "reason"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_transaction_review_reasons_transaction_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_transaction_review_reasons_reason", "transaction_review_reasons", ["reason"]
    )

    op.create_table(
        "foreign_transaction_details",
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("commission_amount", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.Column("stated_fx_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("effective_fx_rate", sa.Numeric(18, 6), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_foreign_transaction_details_transaction_id",
            ondelete="CASCADE",
        ),
    )

    _create_categorisation_tables()
    _create_trip_tables()
    _create_currency_validation_tables()


def _create_categorisation_tables() -> None:
    op.create_table(
        "custom_categories",
        bigint_pk(),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_custom_categories_user_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 60",
            name="ck_custom_categories_name",
        ),
        sa.CheckConstraint("name_key <> 'none'", name="ck_custom_categories_reserved"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_custom_categories_user_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_custom_categories_id_user"),
    )

    op.create_table(
        "transaction_category_overrides",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("custom_category_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "transaction_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_category_overrides_transaction_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_overrides_category_user",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_overrides_category", "transaction_category_overrides", ["custom_category_id"]
    )

    op.create_table(
        "category_mappings",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("bank_category_raw", sa.Text(), nullable=False),
        sa.Column("custom_category_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "bank_category_raw"),
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_mappings_category_user",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "category_rules",
        bigint_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("custom_category_id", sa.BigInteger(), nullable=False),
        created_at(),
        sa.UniqueConstraint("public_id", name="uq_category_rules_public_id"),
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_rules_category_user",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_category_rules_user_id_desc", "category_rules", ["user_id", sa.text("id DESC")]
    )

    op.create_table(
        "category_rule_terms",
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("term_norm", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("rule_id", "term_norm"),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["category_rules.id"],
            name="fk_category_rule_terms_rule_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("term_norm <> ''", name="ck_category_rule_terms_term"),
    )


def _create_trip_tables() -> None:
    op.create_table(
        "trips",
        bigint_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        created_at(),
        sa.UniqueConstraint("public_id", name="uq_trips_public_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_trips_user_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 80", name="ck_trips_name"
        ),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_trips_currency"),
        sa.CheckConstraint("end_date >= start_date", name="ck_trips_dates"),
    )
    op.create_index("ix_trips_user_start", "trips", ["user_id", "start_date"])

    op.create_table(
        "trip_transaction_tags",
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("trip_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_trip_tags_transaction_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_trip_tags_trip_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_trip_tags_trip_id", "trip_transaction_tags", ["trip_id"])

    op.create_table(
        "trip_transaction_exclusions",
        sa.Column("trip_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("trip_id", "transaction_id"),
        sa.ForeignKeyConstraint(
            ["trip_id"], ["trips.id"], name="fk_trip_exclusions_trip_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_trip_exclusions_transaction_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_trip_exclusions_transaction_id", "trip_transaction_exclusions", ["transaction_id"]
    )


def _create_currency_validation_tables() -> None:
    op.create_table(
        "currency_validation_batches",
        bigint_pk(),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("applied_count", sa.Integer(), nullable=False),
        timestamp("created_at"),
        timestamp("undone_at", nullable=True),
        sa.UniqueConstraint("public_id", name="uq_cv_batches_public_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_cv_batches_user_id", ondelete="CASCADE"
        ),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_cv_batches_currency"),
        sa.CheckConstraint("applied_count >= 0", name="ck_cv_batches_applied_count"),
        sa.CheckConstraint(
            "undone_at IS NULL OR undone_at >= created_at", name="ck_batches_undone"
        ),
    )
    op.create_index(
        "ix_cv_batches_user_created", "currency_validation_batches", ["user_id", "created_at"]
    )

    op.create_table(
        "currency_validation_batch_members",
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("batch_id", "transaction_id"),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["currency_validation_batches.id"],
            name="fk_cv_members_batch_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_cv_members_transaction_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_cv_members_transaction", "currency_validation_batch_members", ["transaction_id"]
    )

    op.create_table(
        "currency_validations",
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("validated_by_user_id", sa.BigInteger(), nullable=False),
        timestamp("validated_at"),
        sa.PrimaryKeyConstraint("transaction_id"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_cv_transaction_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["currency_validation_batches.id"],
            name="fk_cv_batch_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["validated_by_user_id"], ["users.id"], name="fk_cv_validated_by_user_id"
        ),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_cv_currency"),
    )
    op.create_index("ix_cv_batch_id", "currency_validations", ["batch_id"])
    op.create_index(
        "ix_cv_user_time", "currency_validations", ["validated_by_user_id", "validated_at"]
    )


def downgrade() -> None:
    """Destructive: drops every table and enum type, and with them all stored data."""
    for table in (
        "currency_validations",
        "currency_validation_batch_members",
        "currency_validation_batches",
        "trip_transaction_exclusions",
        "trip_transaction_tags",
        "trips",
        "category_rule_terms",
        "category_rules",
        "category_mappings",
        "transaction_category_overrides",
        "custom_categories",
        "foreign_transaction_details",
        "transaction_review_reasons",
        "transactions",
        "installment_plans",
        "import_runs",
        "cards",
        "accounts",
        "owners",
        "card_ownership_claims",
        "login_failures",
        "sessions",
        "users",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for name in ENUM_LABELS:
        enum(name).drop(bind, checkfirst=False)

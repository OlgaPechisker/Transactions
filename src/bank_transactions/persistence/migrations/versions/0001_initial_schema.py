"""Initial schema: owners, accounts, cards, import runs, installment plans, transactions.

Revision ID: 0001
Revises:
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
}


def enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUM_LABELS[name], name=name, create_type=False)


def identity_pk() -> sa.Column[int]:
    return sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True)


def created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUM_LABELS:
        enum(name).create(bind, checkfirst=False)

    op.create_table(
        "owners",
        identity_pk(),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("match_key", sa.Text(), nullable=False),
        created_at(),
        sa.UniqueConstraint("match_key", name="uq_owners_match_key"),
    )

    op.create_table(
        "accounts",
        identity_pk(),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("bank_code", sa.Text(), nullable=True),
        sa.Column("account_number_enc", postgresql.BYTEA(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], name="fk_accounts_owner_id"),
        sa.UniqueConstraint(
            "bank_code", "account_number_enc", name="uq_accounts_bank_code_account_number_enc"
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_accounts_id_owner_id"),
    )

    op.create_table(
        "cards",
        identity_pk(),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("issuer_code", sa.String(16), nullable=False),
        sa.Column("last4_enc", postgresql.BYTEA(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.id"], name="fk_cards_owner_id"),
        sa.ForeignKeyConstraint(
            ["account_id", "owner_id"],
            ["accounts.id", "accounts.owner_id"],
            name="fk_cards_account_owner",
        ),
        sa.CheckConstraint(f"issuer_code IN ({ISSUERS})", name="ck_cards_issuer_code"),
    )
    op.create_index("ix_cards_issuer_last4", "cards", ["issuer_code", "last4_enc"])
    op.create_index(
        "ix_cards_account_issuer_last4", "cards", ["account_id", "issuer_code", "last4_enc"]
    )

    op.create_table(
        "import_runs",
        identity_pk(),
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
        sa.CheckConstraint(
            f"issuer_code IS NULL OR issuer_code IN ({ISSUERS})", name="ck_import_runs_issuer_code"
        ),
        sa.CheckConstraint(
            "rows_total >= 0 AND rows_imported >= 0 AND rows_duplicate >= 0 AND rows_skipped >= 0",
            name="ck_import_runs_counts_non_negative",
        ),
    )
    op.create_index("ix_import_runs_status_started_at", "import_runs", ["status", "started_at"])

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
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("import_run_id", sa.Integer(), nullable=False),
        sa.Column("issuer_reference", sa.Text(), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("charge_date", sa.Date(), nullable=False),
        sa.Column("billing_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("billing_currency", sa.CHAR(3), nullable=True),
        sa.Column("original_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("original_currency", sa.CHAR(3), nullable=True),
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


def downgrade() -> None:
    op.drop_table("foreign_transaction_details")
    op.drop_table("transaction_review_reasons")
    op.drop_table("transactions")
    op.drop_table("installment_plans")
    op.drop_table("import_runs")
    op.drop_table("cards")
    op.drop_table("accounts")
    op.drop_table("owners")
    bind = op.get_bind()
    for name in ENUM_LABELS:
        enum(name).drop(bind, checkfirst=False)

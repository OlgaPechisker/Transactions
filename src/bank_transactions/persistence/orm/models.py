"""SQLAlchemy 2 mapping of the plan's schema (section 4).

Object names are unqualified on purpose: the schema is chosen by the connection's search_path,
which lets every test own an isolated schema. The Alembic migration mirrors these definitions and
a test keeps the two in step.
"""

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, MetaData, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column

from bank_transactions.domain.enums import (
    Channel,
    ImportStatus,
    IssuerCode,
    ReviewReason,
    TransactionType,
)

ISSUER_CODES = tuple(code.value for code in IssuerCode)
CURRENCY_FORMAT = "~ '^[A-Z]{3}$'"


class Base(DeclarativeBase):
    metadata = MetaData()


TransactionTypeColumn = postgresql.ENUM(
    TransactionType, name="transaction_type_enum", create_type=False
)
ChannelColumn = postgresql.ENUM(Channel, name="channel_enum", create_type=False)
ReviewReasonColumn = postgresql.ENUM(ReviewReason, name="review_reason_enum", create_type=False)
ImportStatusColumn = postgresql.ENUM(ImportStatus, name="import_status_enum", create_type=False)
IssuerCodeColumn = sa.Enum(IssuerCode, native_enum=False, length=16, create_constraint=False)


def _created_at() -> MappedColumn[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


def _issuer_code_check(table: str, nullable: bool) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{code}'" for code in ISSUER_CODES)
    condition = f"issuer_code IN ({allowed})"
    if nullable:
        condition = f"issuer_code IS NULL OR {condition}"
    return sa.CheckConstraint(condition, name=f"ck_{table}_issuer_code")


class OwnerRow(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    display_name: Mapped[str] = mapped_column(sa.Text)
    match_key: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (sa.UniqueConstraint("match_key", name="uq_owners_match_key"),)


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", name="fk_accounts_owner_id"))
    bank_code: Mapped[str | None] = mapped_column(sa.Text)
    account_number_enc: Mapped[bytes | None] = mapped_column(postgresql.BYTEA)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint(
            "bank_code", "account_number_enc", name="uq_accounts_bank_code_account_number_enc"
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_accounts_id_owner_id"),
        Index(
            "ux_accounts_number_without_bank",
            "account_number_enc",
            unique=True,
            postgresql_where=text("bank_code IS NULL AND account_number_enc IS NOT NULL"),
        ),
    )


class CardRow(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", name="fk_cards_owner_id"))
    account_id: Mapped[int] = mapped_column()
    issuer_code: Mapped[IssuerCode] = mapped_column(IssuerCodeColumn)
    last4_enc: Mapped[bytes] = mapped_column(postgresql.BYTEA)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["account_id", "owner_id"],
            ["accounts.id", "accounts.owner_id"],
            name="fk_cards_account_owner",
        ),
        _issuer_code_check("cards", nullable=False),
        Index("ix_cards_issuer_last4", "issuer_code", "last4_enc"),
        Index("ix_cards_account_issuer_last4", "account_id", "issuer_code", "last4_enc"),
    )


class ImportRunRow(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    file_path: Mapped[str] = mapped_column(sa.Text)
    file_name: Mapped[str] = mapped_column(sa.Text)
    file_sha256: Mapped[str] = mapped_column(sa.String(64))
    issuer_code: Mapped[IssuerCode | None] = mapped_column(IssuerCodeColumn)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[ImportStatus] = mapped_column(ImportStatusColumn)
    rows_total: Mapped[int] = mapped_column(server_default="0")
    rows_imported: Mapped[int] = mapped_column(server_default="0")
    rows_duplicate: Mapped[int] = mapped_column(server_default="0")
    rows_skipped: Mapped[int] = mapped_column(server_default="0")
    error_summary: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        _issuer_code_check("import_runs", nullable=True),
        sa.CheckConstraint(
            "rows_total >= 0 AND rows_imported >= 0 AND rows_duplicate >= 0 AND rows_skipped >= 0",
            name="ck_import_runs_counts_non_negative",
        ),
        Index("ix_import_runs_status_started_at", "status", "started_at"),
    )


class InstallmentPlanRow(Base):
    __tablename__ = "installment_plans"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", name="fk_installment_plans_card_id")
    )
    issuer_reference: Mapped[str | None] = mapped_column(sa.Text)
    merchant_name_norm: Mapped[str] = mapped_column(sa.Text)
    purchase_date: Mapped[date] = mapped_column(sa.Date)
    total_amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2))
    currency: Mapped[str] = mapped_column(sa.CHAR(3))
    total_installments: Mapped[int] = mapped_column(sa.SmallInteger)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
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
        Index(
            "ux_installment_plans_issuer_reference",
            "card_id",
            "issuer_reference",
            unique=True,
            postgresql_where=text("issuer_reference IS NOT NULL"),
        ),
    )


class TransactionRow(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", name="fk_transactions_card_id"))
    import_run_id: Mapped[int] = mapped_column(
        ForeignKey("import_runs.id", name="fk_transactions_import_run_id")
    )
    issuer_reference: Mapped[str | None] = mapped_column(sa.Text)
    transaction_date: Mapped[date] = mapped_column(sa.Date)
    charge_date: Mapped[date] = mapped_column(sa.Date)
    billing_amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2))
    billing_currency: Mapped[str | None] = mapped_column(sa.CHAR(3))
    original_amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(14, 2))
    original_currency: Mapped[str | None] = mapped_column(sa.CHAR(3))
    merchant_name_raw: Mapped[str] = mapped_column(sa.Text)
    merchant_name_norm: Mapped[str] = mapped_column(sa.Text)
    merchant_city: Mapped[str | None] = mapped_column(sa.Text)
    merchant_country: Mapped[str | None] = mapped_column(sa.Text)
    bank_category_raw: Mapped[str | None] = mapped_column(sa.Text)
    transaction_type: Mapped[TransactionType] = mapped_column(TransactionTypeColumn)
    channel: Mapped[Channel | None] = mapped_column(ChannelColumn)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    installment_plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("installment_plans.id", name="fk_transactions_installment_plan_id")
    )
    installment_number: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
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
        Index(
            "ux_transactions_issuer_reference",
            "card_id",
            "transaction_date",
            "issuer_reference",
            "installment_number",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text("issuer_reference IS NOT NULL"),
        ),
        Index("ix_transactions_card_charge_date", "card_id", "charge_date"),
        Index("ix_transactions_card_transaction_date", "card_id", "transaction_date"),
        Index("ix_transactions_merchant_name_norm", "merchant_name_norm"),
        Index("ix_transactions_bank_category_raw", "bank_category_raw"),
        Index("ix_transactions_installment_plan_id", "installment_plan_id"),
    )


class ReviewReasonRow(Base):
    __tablename__ = "transaction_review_reasons"

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "transactions.id",
            name="fk_transaction_review_reasons_transaction_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    reason: Mapped[ReviewReason] = mapped_column(ReviewReasonColumn, primary_key=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (Index("ix_transaction_review_reasons_reason", "reason"),)


class ForeignDetailRow(Base):
    __tablename__ = "foreign_transaction_details"

    transaction_id: Mapped[int] = mapped_column(
        ForeignKey(
            "transactions.id",
            name="fk_foreign_transaction_details_transaction_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    commission_amount: Mapped[Decimal] = mapped_column(sa.Numeric(14, 2), server_default="0")
    stated_fx_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 6))
    effective_fx_rate: Mapped[Decimal] = mapped_column(sa.Numeric(18, 6))

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
    LoginKeyKind,
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
LoginKeyKindColumn = postgresql.ENUM(LoginKeyKind, name="login_key_kind_enum", create_type=False)
IssuerCodeColumn = sa.Enum(IssuerCode, native_enum=False, length=16, create_constraint=False)


def _created_at() -> MappedColumn[datetime]:
    return mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


def _issuer_code_check(table: str, nullable: bool) -> sa.CheckConstraint:
    allowed = ", ".join(f"'{code}'" for code in ISSUER_CODES)
    condition = f"issuer_code IN ({allowed})"
    if nullable:
        condition = f"issuer_code IS NULL OR {condition}"
    return sa.CheckConstraint(condition, name=f"ck_{table}_issuer_code")


def _bigint_pk() -> MappedColumn[int]:
    return mapped_column(sa.BigInteger, sa.Identity(always=True), primary_key=True)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = _bigint_pk()
    public_id: Mapped[str] = mapped_column(sa.Text)
    email: Mapped[str] = mapped_column(sa.Text)
    email_key: Mapped[str] = mapped_column(sa.Text)
    display_name: Mapped[str] = mapped_column(sa.Text)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("public_id", name="uq_users_public_id"),
        sa.UniqueConstraint("email_key", name="uq_users_email_key"),
        sa.CheckConstraint("btrim(display_name) <> ''", name="ck_users_display_name"),
        sa.CheckConstraint("email_key = lower(btrim(email_key))", name="ck_users_email_key"),
    )


class SessionRow(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = _bigint_pk()
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, ForeignKey("users.id", name="fk_sessions_user_id", ondelete="CASCADE")
    )
    token_digest: Mapped[bytes] = mapped_column(postgresql.BYTEA)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("token_digest", name="uq_sessions_token_digest"),
        sa.CheckConstraint("expires_at > created_at", name="ck_sessions_expiry"),
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
    )


class LoginFailureRow(Base):
    __tablename__ = "login_failures"

    id: Mapped[int] = _bigint_pk()
    key_kind: Mapped[LoginKeyKind] = mapped_column(LoginKeyKindColumn)
    key_digest: Mapped[bytes] = mapped_column(postgresql.BYTEA)
    failed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (Index("ix_login_failures_key_time", "key_kind", "key_digest", "failed_at"),)


class ClaimRow(Base):
    __tablename__ = "card_ownership_claims"

    id: Mapped[int] = _bigint_pk()
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("users.id", name="fk_card_ownership_claims_user_id", ondelete="RESTRICT"),
    )
    issuer_code: Mapped[IssuerCode] = mapped_column(IssuerCodeColumn)
    last4_enc: Mapped[bytes] = mapped_column(postgresql.BYTEA)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("issuer_code", "last4_enc", name="uq_claims_issuer_last4"),
        sa.UniqueConstraint("id", "issuer_code", "last4_enc", "user_id", name="uq_claims_key_user"),
        _issuer_code_check("card_ownership_claims", nullable=False),
        Index("ix_claims_user_id", "user_id"),
    )


class OwnerRow(Base):
    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, ForeignKey("users.id", name="fk_owners_user_id", ondelete="RESTRICT")
    )
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    match_key: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("user_id", "match_key", name="uq_owners_user_match_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_owners_id_user"),
        sa.CheckConstraint(
            "(display_name IS NULL) = (match_key IS NULL)", name="ck_owners_placeholder"
        ),
        Index(
            "ux_owners_user_placeholder",
            "user_id",
            unique=True,
            postgresql_where=text("match_key IS NULL"),
        ),
    )


class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column()
    user_id: Mapped[int] = mapped_column(sa.BigInteger)
    bank_code: Mapped[str | None] = mapped_column(sa.Text)
    account_number_enc: Mapped[bytes | None] = mapped_column(postgresql.BYTEA)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["owner_id", "user_id"], ["owners.id", "owners.user_id"], name="fk_accounts_owner_user"
        ),
        sa.UniqueConstraint(
            "user_id", "bank_code", "account_number_enc", name="uq_accounts_user_bank_number"
        ),
        sa.UniqueConstraint("id", "owner_id", name="uq_accounts_id_owner_id"),
        sa.UniqueConstraint("id", "owner_id", "user_id", name="uq_accounts_id_owner_user"),
        Index(
            "ux_accounts_owner_placeholder",
            "owner_id",
            unique=True,
            postgresql_where=text("bank_code IS NULL AND account_number_enc IS NULL"),
        ),
    )


class CardRow(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(sa.Text)
    owner_id: Mapped[int] = mapped_column(ForeignKey("owners.id", name="fk_cards_owner_id"))
    account_id: Mapped[int] = mapped_column()
    user_id: Mapped[int] = mapped_column(sa.BigInteger)
    claim_id: Mapped[int] = mapped_column(sa.BigInteger)
    issuer_code: Mapped[IssuerCode] = mapped_column(IssuerCodeColumn)
    last4_enc: Mapped[bytes] = mapped_column(postgresql.BYTEA)
    display_name: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("public_id", name="uq_cards_public_id"),
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
        _issuer_code_check("cards", nullable=False),
        Index("ix_cards_issuer_last4", "issuer_code", "last4_enc"),
        Index("ix_cards_account_issuer_last4", "account_id", "issuer_code", "last4_enc"),
        Index("ix_cards_claim_id", "claim_id"),
    )


class ImportRunRow(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(sa.Integer, sa.Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(sa.Text)
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
        Index("ix_import_runs_file_sha256", "file_sha256"),
        Index(
            "ux_import_runs_running_sha",
            "file_sha256",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
        sa.UniqueConstraint("public_id", name="uq_import_runs_public_id"),
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
    public_id: Mapped[str] = mapped_column(sa.Text)
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
    source_original_currency: Mapped[str | None] = mapped_column(sa.CHAR(3))
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
        sa.CheckConstraint(
            f"source_original_currency IS NULL OR source_original_currency {CURRENCY_FORMAT}",
            name="ck_transactions_source_original_currency",
        ),
        sa.UniqueConstraint("public_id", name="uq_transactions_public_id"),
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
        Index("ix_transactions_import_run_id", "import_run_id"),
        Index(
            "ix_transactions_card_original_currency_date",
            "card_id",
            "original_currency",
            "transaction_date",
        ),
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


class CustomCategoryRow(Base):
    __tablename__ = "custom_categories"

    id: Mapped[int] = _bigint_pk()
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("users.id", name="fk_custom_categories_user_id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(sa.Text)
    name_key: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 60",
            name="ck_custom_categories_name",
        ),
        sa.CheckConstraint("name_key <> 'none'", name="ck_custom_categories_reserved"),
        sa.UniqueConstraint("user_id", "name_key", name="uq_custom_categories_user_key"),
        sa.UniqueConstraint("id", "user_id", name="uq_custom_categories_id_user"),
    )


class CategoryOverrideRow(Base):
    __tablename__ = "transaction_category_overrides"

    user_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey(
            "transactions.id", name="fk_category_overrides_transaction_id", ondelete="CASCADE"
        ),
        primary_key=True,
    )
    custom_category_id: Mapped[int] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_overrides_category_user",
            ondelete="CASCADE",
        ),
        Index("ix_overrides_category", "custom_category_id"),
    )


class CategoryMappingRow(Base):
    __tablename__ = "category_mappings"

    user_id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True)
    bank_category_raw: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    custom_category_id: Mapped[int] = mapped_column(sa.BigInteger)

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_mappings_category_user",
            ondelete="CASCADE",
        ),
    )


class CategoryRuleRow(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = _bigint_pk()
    public_id: Mapped[str] = mapped_column(sa.Text)
    user_id: Mapped[int] = mapped_column(sa.BigInteger)
    custom_category_id: Mapped[int] = mapped_column(sa.BigInteger)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("public_id", name="uq_category_rules_public_id"),
        sa.ForeignKeyConstraint(
            ["custom_category_id", "user_id"],
            ["custom_categories.id", "custom_categories.user_id"],
            name="fk_category_rules_category_user",
            ondelete="CASCADE",
        ),
        Index("ix_category_rules_user_id_desc", "user_id", text("id DESC")),
    )


class CategoryRuleTermRow(Base):
    __tablename__ = "category_rule_terms"

    rule_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("category_rules.id", name="fk_category_rule_terms_rule_id", ondelete="CASCADE"),
        primary_key=True,
    )
    term_norm: Mapped[str] = mapped_column(sa.Text, primary_key=True)

    __table_args__ = (sa.CheckConstraint("term_norm <> ''", name="ck_category_rule_terms_term"),)


class TripRow(Base):
    __tablename__ = "trips"

    id: Mapped[int] = _bigint_pk()
    public_id: Mapped[str] = mapped_column(sa.Text)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger, ForeignKey("users.id", name="fk_trips_user_id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    currency: Mapped[str] = mapped_column(sa.CHAR(3))
    start_date: Mapped[date] = mapped_column(sa.Date)
    end_date: Mapped[date] = mapped_column(sa.Date)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        sa.UniqueConstraint("public_id", name="uq_trips_public_id"),
        sa.CheckConstraint(
            "name = btrim(name) AND char_length(name) BETWEEN 1 AND 80", name="ck_trips_name"
        ),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_trips_currency"),
        sa.CheckConstraint("end_date >= start_date", name="ck_trips_dates"),
        Index("ix_trips_user_start", "user_id", "start_date"),
    )


class TripTagRow(Base):
    __tablename__ = "trip_transaction_tags"

    transaction_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("transactions.id", name="fk_trip_tags_transaction_id", ondelete="CASCADE"),
        primary_key=True,
    )
    trip_id: Mapped[int] = mapped_column(
        sa.BigInteger, ForeignKey("trips.id", name="fk_trip_tags_trip_id", ondelete="CASCADE")
    )

    __table_args__ = (Index("ix_trip_tags_trip_id", "trip_id"),)


class TripExclusionRow(Base):
    __tablename__ = "trip_transaction_exclusions"

    trip_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("trips.id", name="fk_trip_exclusions_trip_id", ondelete="CASCADE"),
        primary_key=True,
    )
    transaction_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("transactions.id", name="fk_trip_exclusions_transaction_id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (Index("ix_trip_exclusions_transaction_id", "transaction_id"),)


class ValidationBatchRow(Base):
    __tablename__ = "currency_validation_batches"

    id: Mapped[int] = _bigint_pk()
    public_id: Mapped[str] = mapped_column(sa.Text)
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("users.id", name="fk_cv_batches_user_id", ondelete="CASCADE"),
    )
    currency: Mapped[str] = mapped_column(sa.CHAR(3))
    applied_count: Mapped[int] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    undone_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("public_id", name="uq_cv_batches_public_id"),
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_cv_batches_currency"),
        sa.CheckConstraint("applied_count >= 0", name="ck_cv_batches_applied_count"),
        sa.CheckConstraint(
            "undone_at IS NULL OR undone_at >= created_at", name="ck_batches_undone"
        ),
        Index("ix_cv_batches_user_created", "user_id", "created_at"),
    )


class ValidationBatchMemberRow(Base):
    __tablename__ = "currency_validation_batch_members"

    batch_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey(
            "currency_validation_batches.id", name="fk_cv_members_batch_id", ondelete="CASCADE"
        ),
        primary_key=True,
    )
    transaction_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("transactions.id", name="fk_cv_members_transaction_id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (Index("ix_cv_members_transaction", "transaction_id"),)


class CurrencyValidationRow(Base):
    __tablename__ = "currency_validations"

    transaction_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("transactions.id", name="fk_cv_transaction_id", ondelete="CASCADE"),
        primary_key=True,
    )
    batch_id: Mapped[int] = mapped_column(
        sa.BigInteger,
        ForeignKey("currency_validation_batches.id", name="fk_cv_batch_id", ondelete="RESTRICT"),
    )
    currency: Mapped[str] = mapped_column(sa.CHAR(3))
    validated_by_user_id: Mapped[int] = mapped_column(
        sa.BigInteger, ForeignKey("users.id", name="fk_cv_validated_by_user_id")
    )
    validated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(f"currency {CURRENCY_FORMAT}", name="ck_cv_currency"),
        Index("ix_cv_batch_id", "batch_id"),
        Index("ix_cv_user_time", "validated_by_user_id", "validated_at"),
    )

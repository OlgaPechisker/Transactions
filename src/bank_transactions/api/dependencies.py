"""Request-scoped dependencies: authentication, CSRF, paging, sorting and transaction filters."""

import calendar
import hmac
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, Path, Query, Request

from bank_transactions.domain.enums import CategorySource, ReviewReason, TransactionType
from bank_transactions.domain.errors import AuthenticationError, CsrfError, InvalidQueryError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import PageRequest, SortSpec, TransactionFilter
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.interfaces import AuthContext

SESSION_COOKIE = "bt_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def composition(request: Request) -> ApiComposition:
    result: ApiComposition = request.app.state.composition
    return result


def _bearer_token(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Bearer credentials are malformed")
    return token.strip()


def current_user(
    request: Request,
    comp: Annotated[ApiComposition, Depends(composition)],
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """A bearer header alone is used when present (no cookie fallback); otherwise the session
    cookie. Whether the cookie was used is remembered for `csrf_protected`."""
    request.state.via_cookie = authorization is None
    token = (
        _bearer_token(authorization)
        if authorization is not None
        else request.cookies.get(SESSION_COOKIE)
    )
    context = comp.authenticator.authenticate(token) if token else None
    if context is None:
        raise AuthenticationError("Authentication is required")
    return context


def csrf_protected(
    request: Request,
    context: Annotated[AuthContext, Depends(current_user)],
    csrf_token: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
) -> AuthContext:
    """Cookie-authenticated unsafe requests must echo the session's CSRF token."""
    if request.method not in _SAFE_METHODS and request.state.via_cookie and (
        csrf_token is None or not hmac.compare_digest(csrf_token, context.csrf_token)
    ):
        raise CsrfError("Missing or invalid CSRF token")
    return context


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="pageSize")] = 50,
) -> PageRequest:
    return PageRequest(page, page_size)


def sort_param(allowed: Sequence[str], default: str) -> Callable[..., SortSpec]:
    """Builds a dependency for the `sort` query: comma-separated fields, `-` for descending."""

    def parse(sort: Annotated[str, Query()] = default) -> SortSpec:
        fields: list[tuple[str, bool]] = []
        for part in sort.split(","):
            descending = part.startswith("-")
            name = part.removeprefix("-")
            if name not in allowed:
                raise InvalidQueryError(
                    InvalidQueryError.INVALID_SORT, f"Unknown sort field: {name}"
                )
            fields.append((name, descending))
        return SortSpec(tuple(fields))

    return parse


def include_param[E: StrEnum](
    enum_type: type[E], default: frozenset[E]
) -> Callable[..., frozenset[E]]:
    """Builds a dependency for the comma-separated `include` query."""

    default_text = ",".join(sorted(member.value for member in default))

    def parse(include: Annotated[str, Query()] = default_text) -> frozenset[E]:
        try:
            return frozenset(enum_type(part) for part in include.split(",") if part)
        except ValueError:
            raise InvalidQueryError(
                InvalidQueryError.INVALID_FILTER, "include has an unknown value"
            ) from None

    return parse


def _decimal(name: str, value: str | None) -> Decimal | None:
    if value is None:
        return None
    result: Decimal | None
    try:
        result = Decimal(value)
    except InvalidOperation:
        result = None
    if result is None or not result.is_finite():
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, f"{name} is not a number")
    return result


def transaction_filter_params(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    category: Annotated[list[str] | None, Query()] = None,
    bank_category: Annotated[list[str] | None, Query(alias="bankCategory")] = None,
    custom_category: Annotated[list[str] | None, Query(alias="customCategory")] = None,
    category_source: Annotated[list[CategorySource] | None, Query(alias="categorySource")] = None,
    card_id: Annotated[list[str] | None, Query(alias="cardId")] = None,
    currency: Annotated[list[str] | None, Query()] = None,
    billing_currency: Annotated[str | None, Query(alias="billingCurrency")] = None,
    min_amount: Annotated[str | None, Query(alias="minAmount")] = None,
    max_amount: Annotated[str | None, Query(alias="maxAmount")] = None,
    merchant: Annotated[list[str] | None, Query()] = None,
    type: Annotated[list[TransactionType] | None, Query()] = None,
    country: Annotated[list[str] | None, Query()] = None,
    trip_id: Annotated[list[str] | None, Query(alias="tripId")] = None,
    needs_review: Annotated[bool | None, Query(alias="needsReview")] = None,
    review_reason: Annotated[list[ReviewReason] | None, Query(alias="reviewReason")] = None,
) -> TransactionFilter:
    if month is not None:
        year, month_number = int(month[:4]), int(month[5:])
        if not 1 <= month_number <= 12:
            raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, "month is not a month")
        date_from = date(year, month_number, 1)
        date_to = date(year, month_number, calendar.monthrange(year, month_number)[1])
    try:
        return TransactionFilter(
            date_from=date_from,
            date_to=date_to,
            categories=tuple(category or ()),
            bank_categories=tuple(bank_category or ()),
            custom_categories=tuple(custom_category or ()),
            category_sources=tuple(category_source or ()),
            card_ids=tuple(PublicId(value) for value in card_id or ()),
            currencies=tuple(currency or ()),
            billing_currency=billing_currency,
            min_amount=_decimal("minAmount", min_amount),
            max_amount=_decimal("maxAmount", max_amount),
            merchants=tuple(merchant or ()),
            types=tuple(type or ()),
            countries=tuple(country or ()),
            trip_ids=tuple(trip_id or ()),
            needs_review=needs_review,
            review_reasons=tuple(review_reason or ()),
        )
    except ValueError as error:
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, str(error)) from error


CurrentUser = Annotated[AuthContext, Depends(current_user)]
CsrfUser = Annotated[AuthContext, Depends(csrf_protected)]
Composition = Annotated[ApiComposition, Depends(composition)]
Paging = Annotated[PageRequest, Depends(page_params)]
FilterParams = Annotated[TransactionFilter, Depends(transaction_filter_params)]
CardIdPath = Annotated[PublicId, Path(alias="cardId")]
TransactionIdPath = Annotated[PublicId, Path(alias="transactionId")]
TripIdPath = Annotated[PublicId, Path(alias="tripId")]
BatchIdPath = Annotated[PublicId, Path(alias="batchId")]
RuleIdPath = Annotated[PublicId, Path(alias="ruleId")]

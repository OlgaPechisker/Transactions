"""Request-scoped dependencies: authentication, CSRF, paging, sorting and transaction filters.

Parsing here is syntax only (types, patterns, enums); what a filter means is checked by the
domain and the services."""

import calendar
import hmac
from collections.abc import Callable, Collection
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, Path, Query, Request
from pydantic import StringConstraints

from bank_transactions.domain.enums import CategorySource, ReviewReason, TransactionType
from bank_transactions.domain.errors import AuthenticationError, CsrfError, InvalidQueryError
from bank_transactions.domain.ids import PublicId
from bank_transactions.domain.queries import PageRequest, SortSpec, TransactionFilter
from bank_transactions.services.composition import ApiComposition
from bank_transactions.services.interfaces import AuthContext

SESSION_COOKIE = "bt_session"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DECIMAL_PATTERN = r"^-?\d+(\.\d+)?$"

CurrencyOrUnknown = Annotated[str, StringConstraints(pattern=r"^([A-Z]{3}|UNKNOWN)$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]


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
    if (
        request.method not in _SAFE_METHODS
        and request.state.via_cookie
        and (
            csrf_token is None
            or not hmac.compare_digest(csrf_token.encode(), context.csrf_token.encode())
        )
    ):
        raise CsrfError("Missing or invalid CSRF token")
    return context


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="pageSize")] = 50,
) -> PageRequest:
    return PageRequest(page, page_size)


def _parse_sort(request: Request, sort: str, allowed: Collection[str], single: bool) -> SortSpec:
    if len(request.query_params.getlist("sort")) > 1:
        raise InvalidQueryError(InvalidQueryError.INVALID_SORT, "sort must be given once")
    fields: list[tuple[str, bool]] = []
    for part in sort.split(","):
        descending = part.startswith("-")
        if part.removeprefix("-") not in allowed:
            raise InvalidQueryError(InvalidQueryError.INVALID_SORT, "sort has an unsupported field")
        fields.append((part.removeprefix("-"), descending))
    if single and len(fields) != 1:
        raise InvalidQueryError(InvalidQueryError.INVALID_SORT, "sort takes a single field")
    return SortSpec(tuple(fields))


def sort_param(allowed: Collection[str], default: str) -> Callable[..., SortSpec]:
    """Builds a dependency for the `sort` query: comma-separated fields, `-` for descending."""

    def parse(request: Request, sort: Annotated[str, Query()] = default) -> SortSpec:
        return _parse_sort(request, sort, allowed, single=False)

    return parse


def single_sort_param(allowed: Collection[str], default: str) -> Callable[..., SortSpec]:
    """Like `sort_param` for endpoints sortable by exactly one field; lists are INVALID_SORT."""

    def parse(request: Request, sort: Annotated[str, Query()] = default) -> SortSpec:
        return _parse_sort(request, sort, allowed, single=True)

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


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _month_bounds(month: str) -> tuple[date, date]:
    year, month_number = int(month[:4]), int(month[5:])
    if year < 1 or not 1 <= month_number <= 12:
        raise InvalidQueryError(InvalidQueryError.INVALID_FILTER, "month is not a month")
    return date(year, month_number, 1), date(
        year, month_number, calendar.monthrange(year, month_number)[1]
    )


def transaction_filter_params(
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    category: Annotated[list[str] | None, Query()] = None,
    bank_category: Annotated[list[str] | None, Query(alias="bankCategory")] = None,
    custom_category: Annotated[list[str] | None, Query(alias="customCategory")] = None,
    category_source: Annotated[list[CategorySource] | None, Query(alias="categorySource")] = None,
    card_id: Annotated[list[str] | None, Query(alias="cardId")] = None,
    currency: Annotated[list[CurrencyOrUnknown] | None, Query()] = None,
    billing_currency: Annotated[
        str | None, Query(alias="billingCurrency", pattern=r"^[A-Z]{3}$")
    ] = None,
    min_amount: Annotated[
        str | None, Query(alias="minAmount", pattern=_DECIMAL_PATTERN, max_length=40)
    ] = None,
    max_amount: Annotated[
        str | None, Query(alias="maxAmount", pattern=_DECIMAL_PATTERN, max_length=40)
    ] = None,
    merchant: Annotated[list[str] | None, Query()] = None,
    type: Annotated[list[TransactionType] | None, Query()] = None,
    country: Annotated[list[CountryCode] | None, Query()] = None,
    trip_id: Annotated[list[str] | None, Query(alias="tripId")] = None,
    needs_review: Annotated[bool | None, Query(alias="needsReview")] = None,
    review_reason: Annotated[list[ReviewReason] | None, Query(alias="reviewReason")] = None,
) -> TransactionFilter:
    """`month` is ANDed with `from`/`to`: the filter covers their intersection."""
    if month is not None:
        first, last = _month_bounds(month)
        date_from = first if date_from is None else max(date_from, first)
        date_to = last if date_to is None else min(date_to, last)
        if date_from > date_to:
            raise InvalidQueryError(
                InvalidQueryError.INVALID_FILTER, "month does not overlap the from/to range"
            )
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
            min_amount=_decimal(min_amount),
            max_amount=_decimal(max_amount),
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
PublicIdPath = Annotated[PublicId, Path()]

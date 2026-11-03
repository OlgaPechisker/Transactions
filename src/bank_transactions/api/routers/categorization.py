"""Custom categories, bank-category mappings and merchant rules."""

from typing import Annotated

from fastapi import APIRouter, Path

from bank_transactions.api.dependencies import Composition, CsrfUser, CurrentUser, RuleIdPath
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.categorization import (
    BankCategoryDto,
    CategorizationOverviewDto,
    CategoryMappingDto,
    CategoryRuleDto,
    CreateRuleRequest,
    CustomCategoryDto,
    PutMappingRequest,
    ResultingCategoryDto,
    RuleStatsDto,
)
from bank_transactions.services.category_service import CategorizationOverview
from bank_transactions.services.read_models import MappingRecord, RuleRecord

router = APIRouter(tags=["Categorization"])

BankCategoryPath = Annotated[str, Path(alias="bankCategory")]


def _to_rule_dto(rule: RuleRecord) -> CategoryRuleDto:
    return CategoryRuleDto(
        id=rule.public_id,
        terms=list(rule.terms),
        category=rule.category_name,
        stats=RuleStatsDto(matched=rule.matched_count),
    )


def _to_mapping_dto(mapping: MappingRecord) -> CategoryMappingDto:
    return CategoryMappingDto(
        bank_category=mapping.bank_category, custom_category=mapping.category_name
    )


def _to_overview_dto(overview: CategorizationOverview) -> CategorizationOverviewDto:
    mapped_to = {mapping.bank_category: mapping.category_name for mapping in overview.mappings}
    return CategorizationOverviewDto(
        bank_categories=[
            BankCategoryDto(name=bank.name, count=bank.count, mapped_to=mapped_to.get(bank.name))
            for bank in overview.bank_categories
        ],
        rules=[_to_rule_dto(rule) for rule in overview.rules],
        custom_categories=[
            CustomCategoryDto(name=item.name) for item in overview.custom_categories
        ],
        resulting_categories=[
            ResultingCategoryDto(name=item.name, count=item.count)
            for item in overview.resulting_categories
        ],
    )


@router.get("/categorization", operation_id="getCategorization")
def get_categorization(user: CurrentUser, comp: Composition) -> CategorizationOverviewDto:
    return _to_overview_dto(comp.categories.overview(user))


@router.post(
    "/custom-categories",
    operation_id="createCustomCategory",
    status_code=201,
    responses=problem_responses(409, 422),
)
def create_custom_category(
    body: CustomCategoryDto, user: CsrfUser, comp: Composition
) -> CustomCategoryDto:
    return CustomCategoryDto(name=comp.categories.create_category(user, body.name).name)


@router.put(
    # `:path` lets bank categories that contain a slash through.
    "/category-mappings/{bankCategory:path}",
    operation_id="putCategoryMapping",
    responses=problem_responses(404, 422),
)
def put_category_mapping(
    bank_category: BankCategoryPath, body: PutMappingRequest, user: CsrfUser, comp: Composition
) -> CategoryMappingDto:
    return _to_mapping_dto(comp.categories.put_mapping(user, bank_category, body.custom_category))


@router.delete(
    # `:path` lets bank categories that contain a slash through.
    "/category-mappings/{bankCategory:path}",
    operation_id="deleteCategoryMapping",
    status_code=204,
    responses=problem_responses(404),
)
def delete_category_mapping(
    bank_category: BankCategoryPath, user: CsrfUser, comp: Composition
) -> None:
    comp.categories.delete_mapping(user, bank_category)


@router.post(
    "/category-rules",
    operation_id="createCategoryRule",
    status_code=201,
    responses=problem_responses(422),
)
def create_category_rule(
    body: CreateRuleRequest, user: CsrfUser, comp: Composition
) -> CategoryRuleDto:
    return _to_rule_dto(comp.categories.create_rule(user, body.terms, body.category))


@router.delete(
    "/category-rules/{ruleId}",
    operation_id="deleteCategoryRule",
    status_code=204,
    responses=problem_responses(404),
)
def delete_category_rule(rule_id: RuleIdPath, user: CsrfUser, comp: Composition) -> None:
    comp.categories.delete_rule(user, rule_id)

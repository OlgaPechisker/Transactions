"""Custom categories, bank-category mappings and merchant rules."""

from typing import Annotated, Any

from fastapi import APIRouter, Path

from bank_transactions.api.dependencies import Composition, CsrfUser, CurrentUser, RuleIdPath
from bank_transactions.api.schemas.categorization import (
    CreateRuleRequest,
    CustomCategoryDto,
    PutMappingRequest,
)

router = APIRouter(tags=["Categorization"])

BankCategoryPath = Annotated[str, Path(alias="bankCategory")]


@router.get("/categorization", operation_id="getCategorization")
def get_categorization(user: CurrentUser, comp: Composition) -> Any:
    comp.categories.overview(user)
    raise NotImplementedError


@router.post("/custom-categories", operation_id="createCustomCategory", status_code=201)
def create_custom_category(
    body: CustomCategoryDto, user: CsrfUser, comp: Composition
) -> CustomCategoryDto:
    comp.categories.create_category(user, body.name)
    raise NotImplementedError


@router.put("/category-mappings/{bankCategory}", operation_id="putCategoryMapping")
def put_category_mapping(
    bank_category: BankCategoryPath, body: PutMappingRequest, user: CsrfUser, comp: Composition
) -> Any:
    comp.categories.put_mapping(user, bank_category, body.custom_category)
    raise NotImplementedError


@router.delete(
    "/category-mappings/{bankCategory}", operation_id="deleteCategoryMapping", status_code=204
)
def delete_category_mapping(
    bank_category: BankCategoryPath, user: CsrfUser, comp: Composition
) -> None:
    comp.categories.delete_mapping(user, bank_category)
    raise NotImplementedError


@router.post("/category-rules", operation_id="createCategoryRule", status_code=201)
def create_category_rule(body: CreateRuleRequest, user: CsrfUser, comp: Composition) -> Any:
    comp.categories.create_rule(user, body.terms, body.category)
    raise NotImplementedError


@router.delete("/category-rules/{ruleId}", operation_id="deleteCategoryRule", status_code=204)
def delete_category_rule(rule_id: RuleIdPath, user: CsrfUser, comp: Composition) -> None:
    comp.categories.delete_rule(user, rule_id)
    raise NotImplementedError

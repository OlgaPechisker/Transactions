"""Categorization DTOs."""

from typing import Annotated

from pydantic import ConfigDict, Field

from bank_transactions.api.schemas.common import ApiModel


class CustomCategoryDto(ApiModel):
    name: str = Field(min_length=1, max_length=60)


class PutMappingRequest(ApiModel):
    custom_category: str


class CategoryMappingDto(ApiModel):
    bank_category: str
    custom_category: str


class CreateRuleRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    terms: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    category: str


class RuleStatsDto(ApiModel):
    matched: int = Field(ge=0)


class CategoryRuleDto(ApiModel):
    id: str
    terms: list[str]
    category: str
    stats: RuleStatsDto


class BankCategoryDto(ApiModel):
    name: str
    count: int = Field(ge=0)
    mapped_to: str | None


class ResultingCategoryDto(ApiModel):
    name: str
    count: int = Field(ge=0)


class CategorizationOverviewDto(ApiModel):
    bank_categories: list[BankCategoryDto]
    rules: list[CategoryRuleDto]
    custom_categories: list[CustomCategoryDto]
    resulting_categories: list[ResultingCategoryDto]

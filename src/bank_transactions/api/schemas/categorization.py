"""Categorization DTOs."""

from pydantic import ConfigDict, Field

from bank_transactions.api.schemas.common import ApiModel


class CustomCategoryDto(ApiModel):
    name: str = Field(min_length=1, max_length=60)


class PutMappingRequest(ApiModel):
    custom_category: str


class CreateRuleRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    terms: list[str] = Field(min_length=1)
    category: str

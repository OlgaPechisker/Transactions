"""Typed identifiers so database ids and ciphertext cannot be mixed up."""

from typing import NewType

OwnerId = NewType("OwnerId", int)
AccountId = NewType("AccountId", int)
CardId = NewType("CardId", int)
ImportRunId = NewType("ImportRunId", int)
InstallmentPlanId = NewType("InstallmentPlanId", int)
TransactionId = NewType("TransactionId", int)
UserId = NewType("UserId", int)
SessionId = NewType("SessionId", int)
ClaimId = NewType("ClaimId", int)
TripId = NewType("TripId", int)
RuleId = NewType("RuleId", int)
BatchId = NewType("BatchId", int)
CustomCategoryId = NewType("CustomCategoryId", int)

IdentifierToken = NewType("IdentifierToken", bytes)
PublicId = NewType("PublicId", str)

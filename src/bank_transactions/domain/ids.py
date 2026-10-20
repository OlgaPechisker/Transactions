"""Typed identifiers so database ids and ciphertext cannot be mixed up."""

from typing import NewType

OwnerId = NewType("OwnerId", int)
AccountId = NewType("AccountId", int)
CardId = NewType("CardId", int)
ImportRunId = NewType("ImportRunId", int)
InstallmentPlanId = NewType("InstallmentPlanId", int)
TransactionId = NewType("TransactionId", int)

IdentifierToken = NewType("IdentifierToken", bytes)

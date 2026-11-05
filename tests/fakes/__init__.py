"""Reusable fakes for the API units (K9). Import as `tests.fakes`."""

from tests.fakes.auth import FakeAuthenticator, FakeReviewCounter, make_auth_context
from tests.fakes.clock import FakeClock
from tests.fakes.identity import FakeCatalogue, FakeHasher, FakeTokenService
from tests.fakes.unit_of_work import (
    InMemoryUnitOfWork,
    InMemoryUnitOfWorkFactory,
    RepositoryNotInjectedError,
    RepositorySlots,
)

__all__ = [
    "FakeAuthenticator",
    "FakeCatalogue",
    "FakeClock",
    "FakeHasher",
    "FakeReviewCounter",
    "FakeTokenService",
    "InMemoryUnitOfWork",
    "InMemoryUnitOfWorkFactory",
    "RepositoryNotInjectedError",
    "RepositorySlots",
    "make_auth_context",
]

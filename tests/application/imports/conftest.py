"""Fixtures for the import API service tests: a service over the shared in-memory world, and the
smallest valid .xlsx container for uploads. Helpers hang off the `api` fixture because sibling
test modules cannot be imported."""

import io
import zipfile
from typing import Any

import pytest
from tests.fakes import FakeClock, FakeTokenService, make_auth_context

from bank_transactions.domain.enums import IdentifierType, IssuerCode
from bank_transactions.domain.ids import CardId, PublicId, UserId
from bank_transactions.domain.keys import OwnerMatchKey
from bank_transactions.persistence.commands import NewAccount, NewCard, NewOwner, NewUser
from bank_transactions.services.import_api_service import (
    ApiSelection,
    ImportApiService,
    ImportOutcome,
    SelectionAction,
    StatementUpload,
)
from bank_transactions.services.interfaces import AuthContext

CONTENT_TYPES = (
    '<Types><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.spreadsheetml.sheet.main+xml"/></Types>'
)


def xlsx_container(marker: str = "") -> bytes:
    """A ZIP with the OOXML content types part; `marker` makes distinct files."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("marker.txt", marker)
    return buffer.getvalue()


class ApiWorld:
    """The shared `world` plus the API service, the signed-in caller and a second user."""

    def __init__(self, world: Any) -> None:
        self.world = world
        self.clock = FakeClock(world.clock.now)
        self.service = ImportApiService(
            world.factory,
            world.reader,
            world.selector,
            world.protector,
            FakeTokenService(),
            self.clock,
        )
        self.caller: AuthContext = make_auth_context(
            user_id=world.user.user_id, user_display_name="Dana"
        )
        self.other_id = self._new_user("omer@example.com", "Omer")

    def _new_user(self, email: str, name: str) -> UserId:
        with self.world.factory.create() as uow:
            user = uow.users.create(NewUser(email, email, name, "hash"))
            uow.commit()
        return user.user_id

    def run(
        self,
        selections: tuple[ApiSelection, ...] = (),
        dry_run: bool = False,
        data: bytes | None = None,
    ) -> ImportOutcome:
        upload = StatementUpload(xlsx_container() if data is None else data)
        return self.service.run(self.caller, upload, dry_run, selections)

    def seed_card_for_other_user(self, last4: str = "1111") -> CardId:
        """A card (and its claim) that only the second user can see."""
        world = self.world
        last4_enc = world.token(IdentifierType.CARD_LAST4, last4)
        with world.factory.create() as uow:
            key = OwnerMatchKey.from_display_name("Omer Cohen")
            owner = uow.owners.create(NewOwner("Omer Cohen", key))
            account = uow.accounts.create(NewAccount(owner.owner_id, None, None, None))
            claim = uow.claims.create_or_get(self.other_id, IssuerCode.MAX, last4_enc)
            card = uow.cards.create(
                NewCard(
                    owner.owner_id,
                    account.account_id,
                    IssuerCode.MAX,
                    last4_enc,
                    None,
                    claim.claim_id,
                )
            )
            uow.commit()
        return card.card_id

    def public_id_of(self, card_id: CardId) -> PublicId:
        return PublicId(f"crd_{card_id}")

    @staticmethod
    def create_card(question_id: str, nickname: str | None = None) -> ApiSelection:
        return ApiSelection(question_id, SelectionAction.CREATE_CARD, nickname=nickname)

    @staticmethod
    def use_card(question_id: str, card_id: PublicId) -> ApiSelection:
        return ApiSelection(question_id, SelectionAction.USE_EXISTING_CARD, card_id=card_id)

    @staticmethod
    def xlsx(marker: str = "") -> bytes:
        return xlsx_container(marker)


@pytest.fixture
def api(world: Any) -> ApiWorld:
    return ApiWorld(world)

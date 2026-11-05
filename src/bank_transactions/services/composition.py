"""The services the API consumes, built once by the composition root (library-free)."""

from dataclasses import dataclass

from bank_transactions.services.analysis_service import AnalysisService
from bank_transactions.services.card_service import CardPresenter, CardService
from bank_transactions.services.category_service import CategoryService
from bank_transactions.services.currency_validation_service import CurrencyValidationService
from bank_transactions.services.import_api_service import ImportApiService
from bank_transactions.services.interfaces import Authenticator
from bank_transactions.services.profile_service import ProfileService
from bank_transactions.services.session_service import SessionService
from bank_transactions.services.transaction_service import TransactionService
from bank_transactions.services.trip_service import TripService


@dataclass(frozen=True)
class ApiComposition:
    authenticator: Authenticator
    card_presenter: CardPresenter
    sessions: SessionService
    profile: ProfileService
    cards: CardService
    imports: ImportApiService
    transactions: TransactionService
    categories: CategoryService
    trips: TripService
    analysis: AnalysisService
    currency_validation: CurrencyValidationService

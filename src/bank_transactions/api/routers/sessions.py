"""Sign-in and sign-out."""

from fastapi import APIRouter, Request, Response

from bank_transactions.api.dependencies import Composition, CsrfUser
from bank_transactions.api.schemas.auth import SessionResponse, SignInRequest

router = APIRouter(tags=["Auth"])


@router.post("/sessions", operation_id="createSession", status_code=201)
def create_session(
    body: SignInRequest, request: Request, response: Response, comp: Composition
) -> SessionResponse:
    client_ip = request.client.host if request.client else "unknown"
    comp.sessions.sign_in(body.email, body.password, client_ip)
    raise NotImplementedError


@router.delete("/sessions/current", operation_id="deleteCurrentSession", status_code=204)
def delete_current_session(user: CsrfUser, comp: Composition) -> None:
    comp.sessions.sign_out(user)
    raise NotImplementedError

"""Sign-in and sign-out."""

from fastapi import APIRouter, Request, Response

from bank_transactions.api.dependencies import SESSION_COOKIE, Composition, CsrfUser
from bank_transactions.api.problems import problem_responses
from bank_transactions.api.schemas.auth import SessionResponse, SignInRequest, to_me_dto
from bank_transactions.services.session_service import SESSION_LIFETIME

router = APIRouter(tags=["Auth"])

COOKIE_PATH = "/api"


@router.post(
    "/sessions",
    operation_id="createSession",
    status_code=201,
    responses=problem_responses(400, 429),
)
def create_session(
    body: SignInRequest, request: Request, response: Response, comp: Composition
) -> SessionResponse:
    client_ip = request.client.host if request.client else "unknown"
    issued = comp.sessions.sign_in(body.email, body.password, client_ip)
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path=COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return SessionResponse(
        token=issued.token,
        expires_at=issued.expires_at,
        user=to_me_dto(comp.profile.me(issued.context)),
    )


@router.delete("/sessions/current", operation_id="deleteCurrentSession", status_code=204)
def delete_current_session(user: CsrfUser, comp: Composition, response: Response) -> None:
    comp.sessions.sign_out(user)
    response.delete_cookie(
        SESSION_COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax"
    )

"""POST /imports and GET /imports on the Postgres-backed app, with the real example workbooks, in
both auth modes (bearer and cookie)."""

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

import pytest
from openpyxl import Workbook
from tests.contracts.openapi_conformance import assert_operations_conform
from tests.integration.api.conftest import PgSession, PgSignIn

from bank_transactions.persistence.commands import NewImportRun
from bank_transactions.services.import_api_service import MAX_UPLOAD_BYTES

pytestmark = pytest.mark.postgres

URL = "/api/v1/imports"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIB = 1024 * 1024


def upload(
    session: PgSession,
    data: bytes,
    dry_run: bool,
    selections: object = None,
    raw_selections: str | None = None,
):
    form = {"dryRun": "true" if dry_run else "false"}
    if raw_selections is not None:
        form["identitySelections"] = raw_selections
    elif selections is not None:
        form["identitySelections"] = json.dumps(selections)
    return session.client.post(
        URL,
        files={"file": ("statement.xlsx", data, XLSX)},
        data=form,
        headers=session.unsafe_headers,
    )


def answering(result: dict) -> list[dict]:
    return [
        {"questionId": q["id"], "action": "CREATE_CARD", "nickname": None}
        for q in result["identityQuestions"]
    ]


def import_for_real(session: PgSession, data: bytes) -> dict:
    first = upload(session, data, dry_run=False).json()
    response = upload(session, data, dry_run=False, selections=answering(first))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def isracard(examples) -> bytes:
    return examples["isracard"].read_bytes()


@pytest.fixture
def monthly_max(examples) -> bytes:
    return examples["max_monthly"].read_bytes()


def not_a_statement() -> bytes:
    buffer = io.BytesIO()
    Workbook().save(buffer)
    return buffer.getvalue()


def container_without_workbook() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types><Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>',
        )
    return buffer.getvalue()


# --- a statement that needs identity answers ----------------------------------------------


def test_a_new_statement_comes_back_as_questions_and_commits_nothing(
    pg_signed_in: PgSession, isracard, scalar
):
    response = upload(pg_signed_in, isracard, dry_run=False)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "NEEDS_INPUT"
    assert (body["dryRun"], body["issuer"]) == (False, "ISRACARD")
    assert body["sha256"] == hashlib.sha256(isracard).hexdigest()
    assert body["counts"] == {
        "read": 0,
        "imported": 0,
        "duplicate": 0,
        "skipped": 0,
        "needsReview": 0,
        "needsReviewByReason": [],
    }
    assert body["identityQuestions"]
    for question in body["identityQuestions"]:
        assert question["reason"] == "NO_MATCHING_CARD"
        assert question["issuer"] == "ISRACARD"
        assert len(question["last4"]) == 4
        assert question["candidates"] == []
        assert question["allowedActions"] == ["CREATE_CARD"]
    assert scalar("SELECT count(*) FROM card_ownership_claims") == 0
    assert scalar("SELECT count(*) FROM cards") == 0
    assert scalar("SELECT count(*) FROM transactions") == 0


def test_the_response_has_a_location_header_and_hides_internal_details(
    pg_signed_in: PgSession, isracard
):
    response = upload(pg_signed_in, isracard, dry_run=True)

    body = response.json()
    assert response.headers["location"] == f"{URL}/{body['id']}"
    assert body["id"].startswith("imp_")
    assert set(body) == {
        "id",
        "status",
        "dryRun",
        "issuer",
        "sha256",
        "startedAt",
        "counts",
        "warnings",
        "issues",
        "identityQuestions",
    }
    assert body["startedAt"].endswith("Z")


def test_the_same_questions_come_back_for_the_same_file(pg_signed_in: PgSession, isracard):
    first = upload(pg_signed_in, isracard, dry_run=True).json()
    second = upload(pg_signed_in, isracard, dry_run=False).json()

    assert [q["id"] for q in first["identityQuestions"]] == [
        q["id"] for q in second["identityQuestions"]
    ]


# --- dry run and real import --------------------------------------------------------------


def test_a_dry_run_with_answers_reports_the_import_and_commits_nothing(
    pg_signed_in: PgSession, isracard, scalar
):
    asked = upload(pg_signed_in, isracard, dry_run=True).json()

    response = upload(pg_signed_in, isracard, dry_run=True, selections=answering(asked))

    body = response.json()
    assert response.status_code == 201
    assert (body["status"], body["dryRun"], body["identityQuestions"]) == ("SUCCESS", True, [])
    assert (body["counts"]["read"], body["counts"]["imported"]) == (4, 4)
    for table in ("card_ownership_claims", "cards", "owners", "accounts", "transactions"):
        assert scalar(f"SELECT count(*) FROM {table}") == 0, table
    assert pg_signed_in.client.get(URL).json()["items"] == []


def test_a_real_import_stores_the_rows_and_shows_up_in_the_history(
    pg_signed_in: PgSession, isracard, scalar
):
    body = import_for_real(pg_signed_in, isracard)

    assert (body["status"], body["dryRun"], body["issuer"]) == ("SUCCESS", False, "ISRACARD")
    assert body["counts"]["imported"] == 4
    assert body["counts"]["needsReviewByReason"] == []
    assert scalar("SELECT count(*) FROM transactions") == 4
    history = pg_signed_in.client.get(URL).json()
    assert history["meta"] == {"page": 1, "pageSize": 50, "totalItems": 1, "totalPages": 1}
    (item,) = history["items"]
    assert item == {
        "id": body["id"],
        "sha256": body["sha256"],
        "startedAt": body["startedAt"],
        "status": "SUCCESS",
        "rowsImported": 4,
    }


def test_importing_the_same_file_again_finds_only_duplicates_and_asks_nothing(
    pg_signed_in: PgSession, isracard
):
    first = import_for_real(pg_signed_in, isracard)

    again = upload(pg_signed_in, isracard, dry_run=False).json()

    assert again["status"] == "SUCCESS"
    assert again["identityQuestions"] == []
    assert (again["counts"]["imported"], again["counts"]["duplicate"]) == (0, 4)
    assert again["id"] != first["id"]
    assert [i["id"] for i in pg_signed_in.client.get(URL).json()["items"]] == [first["id"]]


def test_cards_created_by_an_import_hang_from_the_users_placeholder_owner(
    pg_signed_in: PgSession, monthly_max, sql
):
    import_for_real(pg_signed_in, monthly_max)

    owners = sql("SELECT user_id, match_key, display_name FROM owners")
    placeholders = [row for row in owners if row[1] is None]
    assert len(placeholders) <= 1
    assert all(row[0] == pg_signed_in.user.user_id for row in owners)


def test_a_statement_with_several_cards_asks_one_question_each(
    pg_signed_in: PgSession, monthly_max, scalar
):
    asked = upload(pg_signed_in, monthly_max, dry_run=True).json()

    assert len(asked["identityQuestions"]) == 2
    body = import_for_real(pg_signed_in, monthly_max)
    assert (body["counts"]["read"], body["counts"]["imported"]) == (158, 158)
    assert scalar("SELECT count(*) FROM cards") == 2


def test_reimporting_a_statement_reuses_the_cards_it_created(
    pg_signed_in: PgSession, monthly_max, scalar
):
    import_for_real(pg_signed_in, monthly_max)

    again = upload(pg_signed_in, monthly_max, dry_run=False).json()

    assert again["status"] == "SUCCESS"
    assert again["counts"]["duplicate"] == 158
    assert scalar("SELECT count(*) FROM cards") == 2


# --- rejected uploads ---------------------------------------------------------------------


def test_a_file_over_twenty_mebibytes_is_too_large(pg_signed_in: PgSession):
    response = upload(pg_signed_in, b"x" * (MAX_UPLOAD_BYTES + 1), dry_run=True)

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "FILE_TOO_LARGE"


def test_a_body_far_over_the_limit_is_refused_from_its_declared_length(pg_signed_in: PgSession):
    response = upload(pg_signed_in, b"x" * (MAX_UPLOAD_BYTES + 2 * MIB), dry_run=True)

    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"


def corrupt_content_types_stream(data: bytes, part: str = "[Content_Types].xml") -> bytes:
    """A real workbook whose `part` has a garbage deflate stream."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(part)
    header = data[info.header_offset : info.header_offset + 30]
    name_length, extra_length = (
        int.from_bytes(header[26:28], "little"),
        int.from_bytes(header[28:30], "little"),
    )
    start = info.header_offset + 30 + name_length + extra_length
    damaged = bytearray(data)
    damaged[start : start + 16] = b"\xff" * 16
    return bytes(damaged)


@pytest.mark.parametrize("data", [b"not a workbook", b"PK\x03\x04broken"])
def test_a_file_that_is_not_an_xlsx_container_is_unsupported(pg_signed_in: PgSession, data):
    response = upload(pg_signed_in, data, dry_run=True)

    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_FILE_TYPE"


def test_a_container_with_a_corrupted_compressed_part_is_unsupported(
    pg_signed_in: PgSession, isracard
):
    response = upload(pg_signed_in, corrupt_content_types_stream(isracard), dry_run=True)

    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_FILE_TYPE"


def test_a_chunked_body_without_a_content_length_is_cut_off_at_the_limit(
    pg_signed_in: PgSession,
):
    def chunks():
        for _ in range(40):
            yield b"x" * MIB

    response = pg_signed_in.client.post(
        URL,
        content=chunks(),
        headers={
            "content-type": "multipart/form-data; boundary=bt",
            **pg_signed_in.unsafe_headers,
        },
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["code"] == "FILE_TOO_LARGE"
    assert response.headers["content-type"].startswith("application/problem+json")


def test_a_workbook_with_a_corrupted_sheet_stream_is_unprocessable(
    pg_signed_in: PgSession, isracard, scalar
):
    sheet = next(
        name
        for name in zipfile.ZipFile(io.BytesIO(isracard)).namelist()
        if name.startswith("xl/worksheets/sheet")
    )

    response = upload(pg_signed_in, corrupt_content_types_stream(isracard, sheet), dry_run=True)

    assert response.status_code == 422
    assert response.json()["code"] == "UNREADABLE_WORKBOOK"
    assert scalar("SELECT count(*) FROM import_runs WHERE status = 'FAILED'") == 1


def test_a_workbook_that_cannot_be_read_is_unprocessable(pg_signed_in: PgSession, scalar):
    response = upload(pg_signed_in, container_without_workbook(), dry_run=True)

    assert response.status_code == 422
    assert response.json()["code"] == "UNREADABLE_WORKBOOK"
    assert scalar("SELECT count(*) FROM import_runs WHERE status = 'FAILED'") == 1


def test_a_workbook_in_no_known_bank_format_is_unprocessable_and_audited(
    pg_signed_in: PgSession, scalar
):
    response = upload(pg_signed_in, not_a_statement(), dry_run=False)

    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_STATEMENT_FORMAT"
    assert scalar("SELECT count(*) FROM import_runs WHERE status = 'FAILED'") == 1


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ("{not json", "identitySelections"),
        ('{"questionId": "q"}', "identitySelections"),
        ('[{"questionId": "q", "action": "SHRUG"}]', "identitySelections[0]"),
        (
            json.dumps([{"questionId": "q", "action": "CREATE_CARD", "nickname": "n" * 121}]),
            "identitySelections[0].CREATE_CARD.nickname",
        ),
        (
            json.dumps([{"questionId": "q", "action": "USE_EXISTING_CARD"}]),
            "identitySelections[0].USE_EXISTING_CARD.cardId",
        ),
    ],
)
def test_malformed_selections_are_a_validation_error(pg_signed_in: PgSession, isracard, raw, field):
    response = upload(pg_signed_in, isracard, dry_run=True, raw_selections=raw)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [e["field"] for e in body["errors"]] == [field]


def test_the_upload_checks_come_before_the_selection_syntax(pg_signed_in: PgSession):
    response = upload(pg_signed_in, b"not a workbook", dry_run=True, raw_selections="{bad")

    assert response.status_code == 415


def test_a_missing_file_or_dry_run_flag_is_a_validation_error(pg_signed_in: PgSession, isracard):
    no_file = pg_signed_in.client.post(
        URL, data={"dryRun": "true"}, headers=pg_signed_in.unsafe_headers
    )
    no_flag = pg_signed_in.client.post(
        URL, files={"file": ("s.xlsx", isracard, XLSX)}, headers=pg_signed_in.unsafe_headers
    )

    assert (no_file.status_code, no_flag.status_code) == (422, 422)


def test_a_file_already_being_imported_is_a_conflict(pg_signed_in: PgSession, isracard, factory):
    digest = hashlib.sha256(isracard).hexdigest()
    with factory.create() as uow:
        uow.import_runs.start_run(NewImportRun("busy", "busy", digest, datetime.now(UTC)))
        uow.commit()

    response = upload(pg_signed_in, isracard, dry_run=True)

    assert response.status_code == 409
    assert response.json()["code"] == "IMPORT_IN_PROGRESS"


# --- selections that cannot apply ---------------------------------------------------------


def test_a_question_the_file_never_asked_is_a_validation_error(pg_signed_in: PgSession, isracard):
    selections = [{"questionId": "not-a-question", "action": "CREATE_CARD"}]

    response = upload(pg_signed_in, isracard, dry_run=True, selections=selections)

    assert response.status_code == 422
    (error,) = response.json()["errors"]
    assert (error["field"], error["code"]) == (
        "identitySelections[0].questionId",
        "UNKNOWN_QUESTION",
    )


def test_a_question_answered_twice_is_a_validation_error(pg_signed_in: PgSession, isracard):
    asked = upload(pg_signed_in, isracard, dry_run=True).json()
    answers = answering(asked)

    response = upload(pg_signed_in, isracard, dry_run=True, selections=answers + answers[:1])

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "DUPLICATE_QUESTION"


# --- two users ----------------------------------------------------------------------------


def test_another_users_card_key_is_a_conflict_even_in_a_dry_run(
    pg_sign_in: PgSignIn, isracard, scalar
):
    dana = pg_sign_in()
    omer = pg_sign_in("user.b@example.com", "User B")
    import_for_real(dana, isracard)

    for dry_run in (True, False):
        response = upload(omer, isracard, dry_run=dry_run)
        assert response.status_code == 409
        assert response.json()["code"] == "DATA_ALREADY_OWNED"

    assert "user.a" not in response.text
    assert scalar("SELECT count(DISTINCT user_id) FROM card_ownership_claims") == 1
    assert omer.client.get(URL).json()["items"] == []


def test_a_user_cannot_use_a_card_only_another_user_can_see(
    pg_sign_in: PgSignIn, isracard, monthly_max, sql
):
    dana = pg_sign_in()
    omer = pg_sign_in("user.b@example.com", "User B")
    import_for_real(dana, isracard)
    (card_public_id,) = sql("SELECT public_id FROM cards")[0]
    asked = upload(omer, monthly_max, dry_run=True).json()
    selections = [
        {"questionId": asked["identityQuestions"][0]["id"], "action": "USE_EXISTING_CARD"}
        | {"cardId": card_public_id}
    ]

    response = upload(omer, monthly_max, dry_run=True, selections=selections)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_each_user_sees_only_their_own_imports(pg_sign_in: PgSignIn, isracard, monthly_max):
    dana = pg_sign_in()
    omer = pg_sign_in("user.b@example.com", "User B")
    dana_run = import_for_real(dana, isracard)
    omer_run = import_for_real(omer, monthly_max)

    dana_items = dana.client.get(URL).json()["items"]
    omer_items = omer.client.get(URL).json()["items"]

    assert [i["id"] for i in dana_items] == [dana_run["id"]]
    assert [i["id"] for i in omer_items] == [omer_run["id"]]
    assert omer_items[0]["rowsImported"] == 158


# --- history ------------------------------------------------------------------------------


def test_the_history_is_newest_first_by_default_and_sortable_and_paged(
    pg_signed_in: PgSession, isracard, monthly_max
):
    first = import_for_real(pg_signed_in, isracard)
    second = import_for_real(pg_signed_in, monthly_max)
    client = pg_signed_in.client

    newest = [i["id"] for i in client.get(URL).json()["items"]]
    oldest = [i["id"] for i in client.get(URL, params={"sort": "startedAt"}).json()["items"]]
    page_two = client.get(URL, params={"pageSize": 1, "page": 2}).json()

    assert newest == [second["id"], first["id"]]
    assert oldest == [first["id"], second["id"]]
    assert [i["id"] for i in page_two["items"]] == [first["id"]]
    assert page_two["meta"] == {"page": 2, "pageSize": 1, "totalItems": 2, "totalPages": 2}


@pytest.mark.parametrize("sort", ["startedAt,-startedAt", "status", "-"])
def test_an_unsupported_sort_is_a_bad_request(pg_signed_in: PgSession, sort):
    response = pg_signed_in.client.get(URL, params={"sort": sort})

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SORT"


def test_a_repeated_sort_parameter_is_a_bad_request(pg_signed_in: PgSession):
    response = pg_signed_in.client.get(URL, params=[("sort", "startedAt"), ("sort", "-startedAt")])

    assert response.status_code == 400


def test_a_page_size_out_of_range_is_a_bad_request(pg_signed_in: PgSession):
    assert pg_signed_in.client.get(URL, params={"pageSize": 0}).status_code == 400


# --- authentication -----------------------------------------------------------------------


def test_the_history_needs_a_session(pg_client):
    assert pg_client.get(URL).status_code == 401


def test_an_upload_needs_a_session(pg_client, isracard):
    response = pg_client.post(
        URL, files={"file": ("s.xlsx", isracard, XLSX)}, data={"dryRun": "true"}
    )

    assert response.status_code == 401


@pytest.mark.parametrize("auth_mode", ["cookie"])
def test_a_cookie_upload_without_the_csrf_token_is_forbidden(pg_signed_in: PgSession, isracard):
    response = pg_signed_in.client.post(
        URL, files={"file": ("s.xlsx", isracard, XLSX)}, data={"dryRun": "true"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_VALIDATION_FAILED"


def test_the_import_operations_match_the_contract(pg_app):
    assert_operations_conform(pg_app, "runImport", "listImports")

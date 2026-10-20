"""Durable audit of import runs. Every write uses its own committed unit of work, so the record
survives a domain rollback (dry run, needs-input, failure). Labels are derived from the file hash:
the raw path and file name can contain a person's name or a card number and are never stored."""

import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from bank_transactions.domain.errors import ParseError
from bank_transactions.domain.ids import ImportRunId
from bank_transactions.persistence.commands import ImportRunOutcome, NewImportRun
from bank_transactions.persistence.interfaces import UnitOfWorkFactory

_CHUNK_BYTES = 1024 * 1024
_SAFE_SUFFIX = re.compile(r"\.[a-z0-9]{1,5}")
_STALE_SUMMARY = "import interrupted before it could finish"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        raise ParseError("the workbook file could not be read") from None
    return digest.hexdigest()


def audit_labels(path: Path, sha256: str) -> tuple[str, str]:
    suffix = path.suffix.lower()
    safe_suffix = suffix if _SAFE_SUFFIX.fullmatch(suffix) else ""
    return f"sha256/{sha256}", f"{sha256[:12]}{safe_suffix}"


class AuditTrail:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Callable[[], datetime],
        stale_after: timedelta,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._stale_after = stale_after

    def sweep_stuck_runs(self) -> int:
        now = self._clock()
        with self._uow_factory.create() as uow:
            count = uow.import_runs.sweep_stuck_runs(now - self._stale_after, now, _STALE_SUMMARY)
            uow.commit()
        return count

    def start(self, path: Path, sha256: str) -> ImportRunId:
        file_path, file_name = audit_labels(path, sha256)
        run = NewImportRun(file_path, file_name, sha256, self._clock())
        with self._uow_factory.create() as uow:
            run_id = uow.import_runs.start_run(run)
            uow.commit()
        return run_id

    def finish(self, run_id: ImportRunId, outcome: ImportRunOutcome) -> None:
        with self._uow_factory.create() as uow:
            uow.import_runs.finish_run(run_id, outcome)
            uow.commit()

    def now(self) -> datetime:
        return self._clock()

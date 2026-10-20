import json
import subprocess
import sys
from pathlib import Path

import pytest

import bank_transactions

HEAVY_LIBRARIES = ("sqlalchemy", "openpyxl", "cryptography", "alembic", "psycopg")
DOMAIN_DIRECTORY = Path(bank_transactions.__file__).parent / "domain"
DOMAIN_MODULES = sorted(
    f"bank_transactions.domain.{path.stem}"
    for path in DOMAIN_DIRECTORY.glob("*.py")
    if path.stem != "__init__"
)
CONTRACT_MODULES = [
    "bank_transactions.domain",
    "bank_transactions.ports",
    "bank_transactions.ports.workbook_reader",
    "bank_transactions.ports.parser_selector",
    "bank_transactions.ports.identifier_protector",
    "bank_transactions.parsing.interfaces",
    "bank_transactions.identity.interfaces",
    "bank_transactions.identity.models",
    "bank_transactions.persistence.interfaces",
    "bank_transactions.persistence.commands",
    "bank_transactions.persistence.records",
    "bank_transactions.services.report",
    *DOMAIN_MODULES,
]


def loaded_after_import(module):
    script = "import sys, json; import " + module + "; print(json.dumps(sorted(sys.modules)))"
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    loaded = json.loads(completed.stdout)
    assert module in loaded
    return loaded


def test_every_concrete_domain_module_is_covered():
    assert {
        "bank_transactions.domain.models",
        "bank_transactions.domain.money",
        "bank_transactions.domain.workbook",
        "bank_transactions.domain.keys",
    } <= set(DOMAIN_MODULES)


@pytest.mark.parametrize("module", CONTRACT_MODULES)
def test_contract_modules_do_not_import_heavy_libraries(module):
    loaded = loaded_after_import(module)

    offenders = [name for name in loaded if name.split(".")[0] in HEAVY_LIBRARIES]
    assert offenders == []


@pytest.mark.parametrize("module", DOMAIN_MODULES)
def test_each_domain_module_depends_on_no_other_application_package(module):
    loaded = loaded_after_import(module)

    sibling_packages = [
        name
        for name in loaded
        if name.startswith("bank_transactions.") and not name.startswith("bank_transactions.domain")
    ]
    assert sibling_packages == []


def test_service_report_depends_only_on_domain():
    loaded = loaded_after_import("bank_transactions.services.report")

    outside_domain = [
        name
        for name in loaded
        if name.startswith("bank_transactions.")
        and not name.startswith(("bank_transactions.domain", "bank_transactions.services"))
    ]
    assert outside_domain == []

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import bank_transactions

HEAVY_LIBRARIES = ("sqlalchemy", "openpyxl", "cryptography", "alembic", "psycopg")
WEB_LIBRARIES = ("fastapi", "starlette", "pydantic", "argon2", "pycountry")
DATABASE_LIBRARIES = ("sqlalchemy", "psycopg", "alembic")
PACKAGE_DIRECTORY = Path(bank_transactions.__file__).parent
DOMAIN_DIRECTORY = PACKAGE_DIRECTORY / "domain"
DOMAIN_MODULES = sorted(
    f"bank_transactions.domain.{path.stem}"
    for path in DOMAIN_DIRECTORY.glob("*.py")
    if path.stem != "__init__"
)
SERVICE_MODULES = sorted(
    f"bank_transactions.services.{path.stem}"
    for path in (PACKAGE_DIRECTORY / "services").glob("*.py")
    if path.stem != "__init__"
)
CONTRACT_MODULES = [
    "bank_transactions.domain",
    "bank_transactions.ports",
    "bank_transactions.ports.workbook_reader",
    "bank_transactions.ports.parser_selector",
    "bank_transactions.ports.identifier_protector",
    "bank_transactions.ports.password_hasher",
    "bank_transactions.ports.token_service",
    "bank_transactions.ports.currency_catalogue",
    "bank_transactions.ports.clock",
    "bank_transactions.parsing.interfaces",
    "bank_transactions.identity.interfaces",
    "bank_transactions.identity.models",
    "bank_transactions.persistence.interfaces",
    "bank_transactions.persistence.commands",
    "bank_transactions.persistence.records",
    "bank_transactions.services.report",
    "bank_transactions.services.interfaces",
    "bank_transactions.services.composition",
    "bank_transactions.services.read_models",
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


APPLICATION_RUNTIME_MODULES = [
    "bank_transactions.config",
    "bank_transactions.cli",
    "bank_transactions.services.import_service",
    "bank_transactions.services.identity_gate",
    "bank_transactions.services.audit_trail",
    "bank_transactions.services.deduplication",
    "bank_transactions.services.totals_check",
    "bank_transactions.services.session_service",
    "bank_transactions.services.profile_service",
    "bank_transactions.services.card_service",
    "bank_transactions.services.import_api_service",
    "bank_transactions.services.transaction_service",
    "bank_transactions.services.category_service",
    "bank_transactions.services.trip_service",
    "bank_transactions.services.analysis_service",
    "bank_transactions.services.currency_validation_service",
    "bank_transactions.services.review_counter",
]
CONCRETE_ADAPTER_MODULES = (
    "bank_transactions.bootstrap",
    "bank_transactions.parsing.banks",
    "bank_transactions.parsing.workbook",
    "bank_transactions.persistence.db",
    "bank_transactions.persistence.uow",
    "bank_transactions.persistence.providers",
    "bank_transactions.persistence.repositories",
    "bank_transactions.persistence.visibility",
    "bank_transactions.identity.crypto",
    "bank_transactions.identity.cli_resolver",
    "bank_transactions.identity.argon2_hasher",
    "bank_transactions.identity.tokens",
    "bank_transactions.identity.pycountry_catalogue",
)


@pytest.mark.parametrize("module", APPLICATION_RUNTIME_MODULES)
def test_the_import_service_runtime_loads_no_heavy_library_or_concrete_adapter(module):
    loaded = loaded_after_import(module)

    heavy = [name for name in loaded if name.split(".")[0] in HEAVY_LIBRARIES]
    adapters = [name for name in loaded if name.startswith(CONCRETE_ADAPTER_MODULES)]
    assert (heavy, adapters) == ([], [])


def test_only_the_composition_root_pulls_the_concrete_adapters_in():
    loaded = loaded_after_import("bank_transactions.bootstrap")

    heavy = {name.split(".")[0] for name in loaded if name.split(".")[0] in HEAVY_LIBRARIES}
    assert {"sqlalchemy", "openpyxl", "cryptography"} <= heavy


@pytest.mark.parametrize("module", sorted({*CONTRACT_MODULES, *APPLICATION_RUNTIME_MODULES}))
def test_domain_ports_and_services_load_no_web_framework_or_password_library(module):
    loaded = loaded_after_import(module)

    offenders = [name for name in loaded if name.split(".")[0] in WEB_LIBRARIES]
    assert offenders == []


def imported_modules(path):
    """Every module a file imports, including `from package import module` forms."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def first_party_packages(path):
    return {
        name.split(".")[1]
        for name in imported_modules(path)
        if name.startswith("bank_transactions.") and name.count(".") >= 1
    }


def files_under(package):
    return sorted((PACKAGE_DIRECTORY / package).rglob("*.py"))


def relative_id(path):
    return path.relative_to(PACKAGE_DIRECTORY).as_posix()


@pytest.mark.parametrize("path", files_under("api"), ids=relative_id)
def test_the_api_depends_only_on_services_and_domain(path):
    roots = {name.split(".")[0] for name in imported_modules(path)}

    assert roots & set(DATABASE_LIBRARIES) == set()
    assert first_party_packages(path) <= {"api", "services", "domain"}


def test_importing_the_api_loads_no_database_library_or_implementation():
    loaded = loaded_after_import("bank_transactions.api.app")

    database = [name for name in loaded if name.split(".")[0] in DATABASE_LIBRARIES]
    implementations = [name for name in loaded if name.startswith(CONCRETE_ADAPTER_MODULES)]
    assert (database, implementations) == ([], [])


@pytest.mark.parametrize("path", files_under("persistence/providers"), ids=relative_id)
def test_persistence_providers_depend_only_on_persistence_and_domain(path):
    assert first_party_packages(path) <= {"persistence", "domain"}


@pytest.mark.parametrize(
    "path",
    [path for path in files_under(".") if path.name != "bootstrap.py"],
    ids=relative_id,
)
def test_only_the_composition_root_imports_concrete_classes_from_another_package(path):
    own_package = path.relative_to(PACKAGE_DIRECTORY).parts[0]
    concrete = [
        module
        for module in CONCRETE_ADAPTER_MODULES
        if module != "bank_transactions.bootstrap"
        and module.split(".")[1] != own_package
        and any(name.startswith(module) for name in imported_modules(path))
    ]

    assert concrete == []

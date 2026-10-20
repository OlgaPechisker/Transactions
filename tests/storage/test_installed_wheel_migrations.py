"""Migrations run from a built wheel installed outside the checkout, not from the source tree."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from bank_transactions.persistence.db import create_database_engine

pytestmark = pytest.mark.postgres

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_TABLE_COUNT = 8

MIGRATION_SCRIPT = """
import json, sys
import bank_transactions.persistence.db as db

dsn = __import__("os").environ["BT_TEST_DSN"]
action, schema = sys.argv[1], sys.argv[2]
if action == "upgrade":
    db.upgrade_database(dsn, schema=schema)
elif action == "downgrade":
    db.downgrade_database(dsn, schema=schema)
print(json.dumps({"module": db.__file__}))
"""


@pytest.fixture(scope="module")
def fresh_source_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The project as the wheel must see it: no build/, caches or egg-info from earlier builds
    that could keep removed files alive."""
    copy = tmp_path_factory.mktemp("fresh_source")
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", copy / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        copy / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build"),
    )
    return copy


@pytest.fixture(scope="module")
def installed_site(fresh_source_copy: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    workspace = tmp_path_factory.mktemp("installed")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(fresh_source_copy), "--no-deps", "-q", "-w"]
        + [str(workspace / "wheel")],
        check=True,
        capture_output=True,
    )
    (wheel,) = (workspace / "wheel").glob("*.whl")
    site = workspace / "site"
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "-q", "--target", str(site)]
        + [str(wheel)],
        check=True,
        capture_output=True,
    )
    return site


@pytest.fixture
def run_installed_migration(installed_site: Path, tmp_path: Path, schema_name: str):
    environment = {
        **os.environ,
        "PYTHONPATH": str(installed_site),
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    def run(action: str) -> dict[str, str]:
        completed = subprocess.run(
            [sys.executable, "-c", MIGRATION_SCRIPT, action, schema_name],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        return json.loads(completed.stdout.strip().splitlines()[-1])

    return run


@pytest.fixture
def table_names(postgres_dsn: str, schema_name: str):
    def read() -> set[str]:
        engine = create_database_engine(postgres_dsn, schema=schema_name)
        try:
            with engine.connect() as connection:
                rows = connection.execute(
                    sa.text(
                        "select table_name from information_schema.tables"
                        " where table_schema = current_schema()"
                    )
                )
                return {name for (name,) in rows} - {"alembic_version"}
        finally:
            engine.dispose()

    return read


def test_the_migration_helper_runs_from_the_installed_wheel_not_the_checkout(
    installed_site, run_installed_migration
):
    report = run_installed_migration("probe")

    module = Path(report["module"]).resolve()
    assert module.is_relative_to(installed_site.resolve())
    assert not module.is_relative_to(PROJECT_ROOT)


def test_the_installed_wheel_upgrades_downgrades_and_upgrades_again(
    run_installed_migration, table_names
):
    run_installed_migration("upgrade")
    assert len(table_names()) == DOMAIN_TABLE_COUNT

    run_installed_migration("downgrade")
    assert table_names() == set()

    run_installed_migration("upgrade")
    assert len(table_names()) == DOMAIN_TABLE_COUNT

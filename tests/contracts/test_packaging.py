import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = "bank_transactions/persistence/migrations"
MIGRATION_RESOURCES = {
    f"{MIGRATIONS}/env.py",
    f"{MIGRATIONS}/script.py.mako",
    f"{MIGRATIONS}/versions/0001_initial_schema.py",
}


@pytest.fixture(scope="module")
def wheel_files(tmp_path_factory):
    wheel_dir = tmp_path_factory.mktemp("wheel")
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(PROJECT_ROOT), "--no-deps", "-q", "-w"]
        + [str(wheel_dir)],
        check=True,
        capture_output=True,
    )
    (wheel,) = wheel_dir.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


def test_wheel_ships_the_alembic_migration_resources(wheel_files):
    assert wheel_files >= MIGRATION_RESOURCES


def test_wheel_keeps_the_typing_marker(wheel_files):
    assert "bank_transactions/py.typed" in wheel_files


def test_sqlalchemy_minimum_supports_nulls_not_distinct_indexes():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [Requirement(text) for text in project["project"]["dependencies"]]
    (sqlalchemy,) = [item for item in requirements if item.name.lower() == "sqlalchemy"]

    assert not sqlalchemy.specifier.contains(Version("2.0.15"))
    assert sqlalchemy.specifier.contains(Version("2.0.16"))

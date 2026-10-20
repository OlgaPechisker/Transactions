import shutil
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
SYNTHETIC_PYPROJECT = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "stale-probe"
version = "0.0.1"

[tool.setuptools.packages.find]
where = ["src"]
"""


def wheel_file_names(source_root: Path, work_dir: Path) -> set[str]:
    """Build from a fresh copy of pyproject.toml, the declared readme and src, so build/ output,
    caches and egg-info from earlier builds cannot keep deleted files alive. The isolated build
    needs setuptools from the network or pip's cache."""
    fresh = work_dir / "fresh_source"
    fresh.mkdir(parents=True)
    shutil.copy2(source_root / "pyproject.toml", fresh / "pyproject.toml")
    readme = tomllib.loads((source_root / "pyproject.toml").read_text("utf-8"))["project"].get(
        "readme"
    )
    if isinstance(readme, str):
        shutil.copy2(source_root / readme, fresh / readme)
    shutil.copytree(
        source_root / "src",
        fresh / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", "build"),
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(fresh), "--no-deps", "-q", "-w"]
        + [str(work_dir / "wheel")],
        check=True,
        capture_output=True,
    )
    (wheel,) = (work_dir / "wheel").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        return set(archive.namelist())


@pytest.fixture(scope="module")
def wheel_files(tmp_path_factory):
    return wheel_file_names(PROJECT_ROOT, tmp_path_factory.mktemp("wheel"))


def test_wheel_does_not_ship_modules_that_only_survive_in_stale_build_output(tmp_path):
    project = tmp_path / "project"
    (project / "src" / "probe").mkdir(parents=True)
    (project / "pyproject.toml").write_text(SYNTHETIC_PYPROJECT, encoding="utf-8")
    (project / "src" / "probe" / "__init__.py").write_text("", encoding="utf-8")
    stale_build = project / "build" / "lib" / "probe"
    stale_build.mkdir(parents=True)
    (stale_build / "__init__.py").write_text("", encoding="utf-8")
    (stale_build / "deleted_long_ago.py").write_text("", encoding="utf-8")

    names = wheel_file_names(project, tmp_path / "work")

    assert "probe/__init__.py" in names
    assert "probe/deleted_long_ago.py" not in names


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

"""Header-driven column mapping: aliases per logical column, resolved against a header row."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from bank_transactions.domain.workbook import CellValue, Row
from bank_transactions.parsing.normalizers.text import header_key


@dataclass(frozen=True)
class ColumnSpec:
    """A logical column with every header spelling an issuer is known to use for it."""

    key: str
    aliases: tuple[str, ...]
    required: bool = True

    def matches(self, folded_header: str) -> bool:
        return folded_header in {header_key(alias) for alias in self.aliases}


@dataclass(frozen=True)
class ColumnLayout:
    """Where the header sits and which one-based column holds each logical column."""

    header_row: int
    columns: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", MappingProxyType(dict(self.columns)))

    def has(self, key: str) -> bool:
        return key in self.columns

    def column(self, key: str) -> int:
        return self.columns[key]

    def cell(self, cells: Row, key: str) -> CellValue:
        """The row's value for a logical column; None when the column is absent or short."""
        column = self.columns.get(key)
        if column is None or column > len(cells):
            return None
        return cells[column - 1]


class ColumnMap:
    def __init__(self, *specs: ColumnSpec) -> None:
        keys = [spec.key for spec in specs]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate column key in column map")
        self._specs = specs

    @property
    def required_keys(self) -> tuple[str, ...]:
        return tuple(spec.key for spec in self._specs if spec.required)

    def match(self, row: Row) -> dict[str, int]:
        """Logical column -> one-based index for every spec this row's cells name."""
        found: dict[str, int] = {}
        for index, cell in enumerate(row, start=1):
            folded = header_key(cell)
            if not folded:
                continue
            for spec in self._specs:
                if spec.key not in found and spec.matches(folded):
                    found[spec.key] = index
        return found

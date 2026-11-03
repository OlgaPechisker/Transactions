"""Spending-analysis response DTOs, mirroring `SpendingAnalysis` in the OpenAPI contract."""

from pydantic import Field

from bank_transactions.api.schemas.common import ApiModel, MoneyDto


class AnalysisRangeDto(ApiModel):
    from_: str = Field(alias="from")
    to: str


class MonthlyAverageDto(ApiModel):
    value: MoneyDto | None
    complete_buckets: int
    partial_buckets: list[str]


class AnalysisTotalsDto(ApiModel):
    net: MoneyDto
    count: int
    monthly_average: MonthlyAverageDto


class PreviousGroupDto(ApiModel):
    net: MoneyDto
    count: int


class AnalysisGroupDto(ApiModel):
    key: str | None
    label: str
    net: MoneyDto
    count: int
    previous: PreviousGroupDto | None


class TimePointDto(ApiModel):
    key: str
    start: str
    end: str
    net: MoneyDto
    complete: bool


class PreviousSeriesDto(ApiModel):
    average: MoneyDto | None
    points: list[TimePointDto]


class TimeSeriesDto(ApiModel):
    bucket: str
    average: MoneyDto | None
    points: list[TimePointDto]
    previous: PreviousSeriesDto | None


class MatrixColumnDto(ApiModel):
    key: str
    complete: bool


class MatrixCellDto(ApiModel):
    net: MoneyDto


class MatrixRowDto(ApiModel):
    key: str | None
    label: str
    cells: list[MatrixCellDto]
    total: MoneyDto


class MatrixDto(ApiModel):
    row_dimension: str
    bucket: str
    columns: list[MatrixColumnDto]
    rows: list[MatrixRowDto]
    column_totals: list[MoneyDto]


class SpendingAnalysisDto(ApiModel):
    """`groups`, `timeSeries` and `matrix` are left out of the body when not requested."""

    range: AnalysisRangeDto
    comparison_range: AnalysisRangeDto | None
    totals: AnalysisTotalsDto
    comparison_totals: AnalysisTotalsDto | None
    groups: list[AnalysisGroupDto] | None = None
    time_series: TimeSeriesDto | None = None
    matrix: MatrixDto | None = None

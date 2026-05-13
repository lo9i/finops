"""AWS Cost & Usage Report (CUR) CSV parser with validation.

Returns (records, warnings, rows_total).
Accepted columns (with common aliases):
  lineItem/UsageStartDate, lineItem/UsageEndDate, lineItem/UsageType,
  lineItem/UnblendedCost, lineItem/CurrencyCode, lineItem/ResourceId,
  product/productName, product/region
"""
from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from .schema import NormalizedBilling


_COLUMN_ALIASES = {
    "resource_id": ["lineItem/ResourceId", "ResourceId", "resource_id"],
    "service": ["product/productName", "ProductName", "service"],
    "usage_start": ["lineItem/UsageStartDate", "UsageStartDate", "usage_start"],
    "usage_end": ["lineItem/UsageEndDate", "UsageEndDate", "usage_end"],
    "cost": ["lineItem/UnblendedCost", "UnblendedCost", "cost"],
    "currency": ["lineItem/CurrencyCode", "CurrencyCode", "currency"],
    "region": ["product/region", "ProductRegion", "region"],
    "usage_type": ["lineItem/UsageType", "UsageType", "usage_type"],
    "account_id": ["lineItem/UsageAccountId", "UsageAccountId", "account_id"],
}


def _pick(row: dict, names: list[str]):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _parse_dt(value) -> datetime | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return pd.to_datetime(value, utc=True).to_pydatetime().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def parse_aws_cur(content: bytes | str) -> tuple[list[NormalizedBilling], list[str], int]:
    warnings: list[str] = []
    try:
        if isinstance(content, bytes):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_csv(io.StringIO(content))
    except Exception as exc:
        raise ValueError(f"could not parse CSV: {exc}") from exc

    rows_total = len(df)
    if rows_total == 0:
        warnings.append("File is empty (0 rows).")
        return [], warnings, 0

    # Schema sanity: at least one cost-like column must be present.
    if not any(c in df.columns for c in _COLUMN_ALIASES["cost"]):
        warnings.append(
            "No recognizable cost column found "
            f"(expected one of: {', '.join(_COLUMN_ALIASES['cost'])}). "
            "All rows will have cost=0."
        )
    if not any(c in df.columns for c in _COLUMN_ALIASES["resource_id"]):
        warnings.append(
            "No recognizable resource_id column found "
            f"(expected one of: {', '.join(_COLUMN_ALIASES['resource_id'])}). "
            "Findings cannot be linked to resources without it."
        )

    df = df.where(pd.notna(df), None)

    records: list[NormalizedBilling] = []
    rows_missing_resource_id = 0
    rows_missing_cost = 0
    rows_bad_date = 0

    for idx, raw_row in enumerate(df.to_dict(orient="records"), start=1):
        cost_raw = _pick(raw_row, _COLUMN_ALIASES["cost"])
        try:
            cost = float(cost_raw) if cost_raw is not None else 0.0
        except (TypeError, ValueError):
            cost = 0.0
            rows_missing_cost += 1

        rid = _pick(raw_row, _COLUMN_ALIASES["resource_id"])
        if rid is None:
            rows_missing_resource_id += 1

        usage_start_raw = _pick(raw_row, _COLUMN_ALIASES["usage_start"])
        usage_start = _parse_dt(usage_start_raw)
        if usage_start_raw is not None and usage_start is None:
            rows_bad_date += 1

        account_raw = _pick(raw_row, _COLUMN_ALIASES["account_id"])
        records.append(
            NormalizedBilling(
                provider="aws",
                resource_id=rid,
                service=str(_pick(raw_row, _COLUMN_ALIASES["service"]) or "Unknown"),
                usage_start=usage_start,
                usage_end=_parse_dt(_pick(raw_row, _COLUMN_ALIASES["usage_end"])),
                cost=cost,
                currency=str(_pick(raw_row, _COLUMN_ALIASES["currency"]) or "USD"),
                region=_pick(raw_row, _COLUMN_ALIASES["region"]),
                usage_type=_pick(raw_row, _COLUMN_ALIASES["usage_type"]),
                account_id=str(account_raw) if account_raw is not None else None,
                raw=raw_row,
            )
        )

    if rows_missing_resource_id:
        warnings.append(
            f"{rows_missing_resource_id}/{rows_total} row(s) missing resource_id — "
            "these costs won't roll up to any resource."
        )
    if rows_missing_cost:
        warnings.append(
            f"{rows_missing_cost}/{rows_total} row(s) had unparseable cost; defaulted to 0."
        )
    if rows_bad_date:
        warnings.append(f"{rows_bad_date}/{rows_total} row(s) had unparseable usage dates.")

    return records, warnings, rows_total

"""Azure Cost Management JSON export parser with validation.

Returns (records, warnings, rows_total).
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd

from .schema import NormalizedBilling


_ALIASES = {
    "resource_id": ["ResourceId", "resourceId", "InstanceId", "resource_id"],
    "service": ["ServiceName", "serviceName", "MeterCategory", "service"],
    "usage_start": ["UsageDateTime", "usageDateTime", "Date", "usage_start"],
    "cost": ["PreTaxCost", "preTaxCost", "Cost", "cost"],
    "currency": ["BillingCurrencyCode", "billingCurrencyCode", "Currency", "currency"],
    "region": ["ResourceLocation", "resourceLocation", "region"],
    "usage_type": ["MeterSubCategory", "meterSubCategory", "MeterName", "usage_type"],
    "account_id": ["SubscriptionId", "subscriptionId", "SubscriptionGuid", "account_id"],
}


def _pick(row: dict, names: list[str]):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _parse_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return pd.to_datetime(value, utc=True).to_pydatetime().replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def parse_azure_export(
    content: bytes | str,
) -> tuple[list[NormalizedBilling], list[str], int]:
    warnings: list[str] = []
    if isinstance(content, bytes):
        content = content.decode("utf-8")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse JSON: {exc}") from exc

    if isinstance(payload, dict):
        if "value" in payload and isinstance(payload["value"], list):
            rows = payload["value"]
        elif "properties" in payload and isinstance(payload["properties"], dict):
            rows = payload["properties"].get("rows", [])
        else:
            warnings.append(
                "Unrecognized envelope; treating top-level object as a single record. "
                "Expected an array of records or {value: [...]}."
            )
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("Azure export must be a JSON array or an object containing one.")

    rows_total = len(rows)
    if rows_total == 0:
        warnings.append("File is empty (0 rows).")
        return [], warnings, 0

    records: list[NormalizedBilling] = []
    rows_missing_resource_id = 0
    rows_missing_cost = 0

    for row in rows:
        if not isinstance(row, dict):
            warnings.append("Skipped a non-object row.")
            continue

        cost_raw = _pick(row, _ALIASES["cost"])
        try:
            cost = float(cost_raw) if cost_raw is not None else 0.0
        except (TypeError, ValueError):
            cost = 0.0
            rows_missing_cost += 1

        rid = _pick(row, _ALIASES["resource_id"])
        if rid is None:
            rows_missing_resource_id += 1

        account_raw = _pick(row, _ALIASES["account_id"])
        records.append(
            NormalizedBilling(
                provider="azure",
                resource_id=rid,
                service=str(_pick(row, _ALIASES["service"]) or "Unknown"),
                usage_start=_parse_dt(_pick(row, _ALIASES["usage_start"])),
                usage_end=_parse_dt(_pick(row, _ALIASES["usage_start"])),
                cost=cost,
                currency=str(_pick(row, _ALIASES["currency"]) or "USD"),
                region=_pick(row, _ALIASES["region"]),
                usage_type=_pick(row, _ALIASES["usage_type"]),
                account_id=str(account_raw) if account_raw is not None else None,
                raw=row,
            )
        )

    if rows_missing_resource_id:
        warnings.append(
            f"{rows_missing_resource_id}/{rows_total} row(s) missing ResourceId — "
            "these costs won't roll up to any resource."
        )
    if rows_missing_cost:
        warnings.append(
            f"{rows_missing_cost}/{rows_total} row(s) had unparseable PreTaxCost; defaulted to 0."
        )

    return records, warnings, rows_total

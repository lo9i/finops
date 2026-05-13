"""Runtime configuration. Environment overrides via env vars."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = ROOT / "samples"
DB_PATH = Path(os.environ.get("COST_OPTIMIZER_DB", ROOT / "cost_optimizer.db"))
DB_URL = f"sqlite:///{DB_PATH}"

# Detector thresholds (overridable via env)
# Providers the engine has detectors for. The UI only offers these as filter
# options; data with other providers can still be ingested (it'll surface as a
# warning) but won't appear as selectable in the inventory filter dropdowns.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("aws", "azure")

IDLE_CPU_THRESHOLD_PCT = float(os.environ.get("IDLE_CPU_THRESHOLD_PCT", "5.0"))
IDLE_NET_THRESHOLD_BYTES = float(os.environ.get("IDLE_NET_THRESHOLD_BYTES", "1048576"))  # 1 MiB/day avg
OLD_SNAPSHOT_DAYS = int(os.environ.get("OLD_SNAPSHOT_DAYS", "90"))
# Long-running billed resource thresholds (history-based detector)
UNMONITORED_MIN_DAYS = int(os.environ.get("UNMONITORED_MIN_DAYS", "30"))
UNMONITORED_MIN_RECENT_COST = float(os.environ.get("UNMONITORED_MIN_RECENT_COST", "5.0"))

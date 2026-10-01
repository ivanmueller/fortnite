"""Where the data lives. Both datasets are produced by the pipeline (pipeline/python/flatten.py)."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _env_file(key: str) -> str | None:
    """Read one setting from the project's .env (the dashboard doesn't load it otherwise)."""
    try:
        for line in (REPO / ".env").read_text(encoding="utf-8-sig").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == key and v.strip():
                return v.strip().strip('"')
    except OSError:
        pass
    return None


DATASETS: dict[str, Path] = {
    # Real tournament data: pipeline/run.ps1 pilot -> data/tables
    "real": Path(os.environ.get("FN_DATA_DIR") or _env_file("ZONELAB_DATA_DIR") or REPO / "data"),
    # Your own replays from the Fortnite Demos folder: pipeline/run.ps1 local -> data_local/tables
    "local": Path(os.environ.get("FN_LOCAL_DIR", REPO / "data_local")),
    # Synthetic demo data: npm run demo-data -> data_synthetic/tables
    "demo": Path(os.environ.get("FN_DEMO_DIR", REPO / "data_synthetic")),
}

DATASET_LABELS = {"real": "Tournaments", "local": "My replays", "demo": "Demo"}

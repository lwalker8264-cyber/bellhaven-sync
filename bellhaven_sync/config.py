# Copyright (c) 2026 Logan Walker. All rights reserved. See NOTICE.
"""Settings. Secrets come from environment variables or a local .env file (never committed)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

BASE_URL = os.environ.get("BELLHAVEN_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_BASE = BASE_URL + "/api/v1"
TOKEN = os.environ.get("BELLHAVEN_TOKEN", "")
DB_PATH = Path(os.environ.get("BELLHAVEN_DB", ROOT / "data" / "state.db"))
REVIEW_PORT = int(os.environ.get("REVIEW_PORT", "8000"))

# The parent account every Bellhaven community should roll up to (looked up by name, not hard-coded id).
TARGET_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"

# Website care offerings -> CRM care_type values.
CARE_TYPE_MAP = {
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "independent living": "Independent Living",
}

"""Mengniu price tag recognition service."""

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

__version__ = "0.1.0"
PIPELINE_VERSION = "service-v1"

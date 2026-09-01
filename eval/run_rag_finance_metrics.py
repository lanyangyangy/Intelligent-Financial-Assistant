from __future__ import annotations

# ruff: noqa: E402, I001

import sys
from pathlib import Path

EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.rag_finance_metrics import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

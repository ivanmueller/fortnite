"""
Export every study's findings for an AI to read: findings.md, findings.json and findings.csv in one zip.

  npm run export -- --team "clix, rapid" --as-of 2026-09-26
  npm run export -- --dataset real --region NAC --from-date 2026-08-01 --out nac-august.zip

Options: --dataset (real, local, demo), --team, --as-of (the event's first day, for the game plan), --scheme (scoring.json id),
--from-date, --to-date, --region (repeatable), --min-lobby-strength, --rows (table rows per study in findings.md), --out.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from fnlab.export import main  # noqa: E402

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Line-budget ratchet (RP-0.1 / RP-0.2).

Budgeted files may shrink but never grow. ``line_budgets.json`` maps a
repo-relative path to its maximum allowed line count — set to the file's size
at the moment it was grandfathered. A budget is only ever lowered, never
raised; structural work that must add lines to a frozen file (e.g. ``api.py``)
should instead move code out so the net count stays within budget.

Usage:
    python scripts/ratchets/check_line_budgets.py            # enforce (CI)
    python scripts/ratchets/check_line_budgets.py --update   # lower budgets to
                                                             # current counts
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUDGETS_PATH = Path(__file__).resolve().parent / "line_budgets.json"


def count_lines(path: Path) -> int:
    with path.open(errors="replace") as handle:
        return sum(1 for _ in handle)


def main(argv: list[str]) -> int:
    update = "--update" in argv
    budgets: dict[str, int] = json.loads(BUDGETS_PATH.read_text())

    violations: list[str] = []
    shrunk: dict[str, int] = {}
    missing: list[str] = []

    for rel_path, budget in budgets.items():
        path = REPO_ROOT / rel_path
        if not path.exists():
            missing.append(rel_path)
            continue
        current = count_lines(path)
        if current > budget:
            violations.append(
                f"  {rel_path}: {current} lines (budget {budget}, +{current - budget})"
            )
        elif current < budget:
            shrunk[rel_path] = current

    if update:
        for rel_path, current in shrunk.items():
            budgets[rel_path] = current
        for rel_path in missing:
            del budgets[rel_path]
        BUDGETS_PATH.write_text(json.dumps(dict(sorted(budgets.items())), indent=2) + "\n")
        print(
            f"Budgets updated: {len(shrunk)} lowered, {len(missing)} removed, "
            f"{len(budgets)} remaining."
        )
        if violations:
            print("Files over budget were NOT raised:")
            print("\n".join(violations))
            return 1
        return 0

    if violations:
        print("Line-budget ratchet FAILED — these files grew past their frozen budget:")
        print("\n".join(violations))
        print(
            "\nBudgets only go down. Move code out of the file (see "
            "docs/remediation-program/plan.md) instead of raising the budget."
        )
        return 1

    if missing:
        print(
            "Budgeted files no longer exist (run with --update to drop them): "
            + ", ".join(missing)
        )
    if shrunk:
        print(
            f"{len(shrunk)} budgeted file(s) shrank — run "
            "'python scripts/ratchets/check_line_budgets.py --update' to lock in the gains."
        )
    print(f"Line-budget ratchet OK ({len(budgets)} files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

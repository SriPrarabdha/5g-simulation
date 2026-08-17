#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARTICIPANT_NOTEBOOK = ROOT / "workshop" / "CDOT_UPF_Closed_Loop_Lab.ipynb"
FROZEN_NOTEBOOK = ROOT / "workshop" / "CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb"


def prepare_teams(output_root: Path, teams: int) -> list[Path]:
    if not 4 <= teams <= 6:
        raise ValueError("teams must be between 4 and 6")
    if not PARTICIPANT_NOTEBOOK.is_file() or not FROZEN_NOTEBOOK.is_file():
        raise FileNotFoundError("build the workshop notebooks before preparing team copies")
    prepared: list[Path] = []
    output_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FROZEN_NOTEBOOK, output_root / FROZEN_NOTEBOOK.name)
    for number in range(1, teams + 1):
        team_id = f"team-{number:02d}"
        team_dir = output_root / team_id
        team_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PARTICIPANT_NOTEBOOK, team_dir / PARTICIPANT_NOTEBOOK.name)
        (team_dir / "team_config.json").write_text(
            json.dumps({
                "schema_version": "workshop-team/1.0",
                "team_id": team_id,
                "decision_output": "WorkshopDecision.json",
                "presenter_credentials_available": False,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        prepared.append(team_dir)
    return prepared


def main() -> int:
    parser = argparse.ArgumentParser(description="Create isolated workshop notebook copies for each table.")
    parser.add_argument("--teams", type=int, default=6)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output" / "workshop")
    args = parser.parse_args()
    for directory in prepare_teams(args.output_root, args.teams):
        print(directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

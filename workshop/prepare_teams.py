#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PARTICIPANT_NOTEBOOK=ROOT/"workshop/CDOT_UPF_Closed_Loop_Lab.ipynb"
FROZEN_NOTEBOOK=ROOT/"workshop/CDOT_UPF_Closed_Loop_Lab_Frozen.ipynb"
def prepare_teams(output_root:Path,teams:int)->list[Path]:
    """Prepare 1–35 isolated participant directories; name retained for API compatibility."""
    if not 1<=teams<=35: raise ValueError("participants must be between 1 and 35")
    if not PARTICIPANT_NOTEBOOK.is_file() or not FROZEN_NOTEBOOK.is_file(): raise FileNotFoundError("build workshop notebooks first")
    output_root.mkdir(parents=True,exist_ok=True); shutil.copy2(FROZEN_NOTEBOOK,output_root/FROZEN_NOTEBOOK.name); prepared=[]
    for number in range(1,teams+1):
        participant=f"participant-{number:02d}"; directory=output_root/participant; directory.mkdir(parents=True,exist_ok=True)
        shutil.copy2(PARTICIPANT_NOTEBOOK,directory/PARTICIPANT_NOTEBOOK.name)
        for name in ("jobs","output","reports"): (directory/name).mkdir(exist_ok=True)
        shutil.copy2(ROOT/"pbs/workshop_solver.pbs",directory/"jobs/workshop_solver.pbs")
        shutil.copy2(ROOT/"pbs/workshop_simulator.pbs",directory/"jobs/workshop_simulator.pbs")
        (directory/"team_config.json").write_text(json.dumps({"schema_version":"workshop-participant/2.0","team_id":participant,
          "participant_id":participant,"private_output_root":str((directory/"output").resolve()),"solver_job":"jobs/workshop_solver.pbs",
          "simulator_job":"jobs/workshop_simulator.pbs","replay_json":"reports/twin-replay.json","report_json":"reports/WorkshopReport.json",
          "report_html":"reports/WorkshopReport.html","presenter_credentials_available":False,"policy_publication_available":False},indent=2)+"\n")
        prepared.append(directory)
    return prepared
def main():
    parser=argparse.ArgumentParser(description="Create isolated individual C-DOT workshop workspaces")
    parser.add_argument("--teams","--participants",dest="teams",type=int,default=35); parser.add_argument("--output-root",type=Path,default=ROOT/"output/workshop")
    args=parser.parse_args(); print(*prepare_teams(args.output_root,args.teams),sep="\n"); return 0
if __name__=="__main__": raise SystemExit(main())

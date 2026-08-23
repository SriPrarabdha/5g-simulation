"""Credential-free JSON and HTML workshop report export."""
from __future__ import annotations
import html, json, os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

def export_report(output_dir: str | Path, *, participant_id: str, solver: Mapping[str, Any],
                  simulation: Mapping[str, Any], replay_path: str,
                  advisory_pilot_sentence: str) -> tuple[Path, Path]:
    if not advisory_pilot_sentence.strip(): raise ValueError("complete the advisory-pilot sentence")
    payload = {"schema_version":"workshop-report/1.0","synthetic":True,"participant_id":participant_id,
      "generated_at":datetime.now(timezone.utc).isoformat(),"scope":"new_session_placement_only",
      "established_sessions_migrated":False,"solver":dict(solver),"simulation":dict(simulation),"replay":replay_path,
      "advisory_pilot_sentence":advisory_pilot_sentence,"credentials_included":False,"policy_publication_performed":False}
    root=Path(output_dir); root.mkdir(parents=True,exist_ok=True); json_path=root/"WorkshopReport.json"
    temporary=root/f".WorkshopReport.{os.getpid()}.tmp"; temporary.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n"); os.replace(temporary,json_path)
    html_path=root/"WorkshopReport.html"; html_path.write_text("<!doctype html><meta charset='utf-8'><title>C-DOT Workshop Report</title>"
      "<style>body{font:16px system-ui;max-width:880px;margin:3rem auto;color:#17343f}pre{white-space:pre-wrap;background:#eef6f7;padding:1rem}</style>"
      f"<h1>C-DOT Digital Twin Workshop Report</h1><p><b>Synthetic evidence</b> · {html.escape(participant_id)}</p>"
      "<p>Future-session placement only. Established sessions migrated: no.</p>"
      f"<h2>Advisory-pilot gate</h2><p>{html.escape(advisory_pilot_sentence)}</p><pre>{html.escape(json.dumps(payload,indent=2))}</pre>")
    return json_path,html_path

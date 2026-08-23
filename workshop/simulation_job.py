"""Structured workshop wrapper around one deterministic campaign shard."""
from __future__ import annotations
import argparse, json, os
from datetime import datetime, timezone
from pathlib import Path
from experiments.run_campaign_shard import run_shard
from workshop.jobs import atomic_json, provenance

def main()->int:
    parser=argparse.ArgumentParser(); parser.add_argument("--manifest",type=Path,required=True); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--campaign-id",required=True); parser.add_argument("--seed",type=int,required=True)
    parser.add_argument("--controller",choices=("static","reactive","predictive"),required=True); args=parser.parse_args()
    state=provenance("simulator",args.seed)|{"state":"running","controller":args.controller,"manifest":str(args.manifest)}
    atomic_json(args.output_dir/"status.json",state)
    try:
        shard=run_shard(args.manifest,args.output_dir,args.campaign_id,args.seed,controller=args.controller,skip_existing=True)
        atomic_json(args.output_dir/"reproducibility.json",state|{"published_shard":str(shard)})
        atomic_json(args.output_dir/"status.json",state|{"state":"completed","published_shard":str(shard),"finished_at":datetime.now(timezone.utc).isoformat()})
        print(shard); return 0
    except Exception as error:
        atomic_json(args.output_dir/"status.json",state|{"state":"failed","error":str(error),"finished_at":datetime.now(timezone.utc).isoformat()}); raise
if __name__=="__main__": raise SystemExit(main())

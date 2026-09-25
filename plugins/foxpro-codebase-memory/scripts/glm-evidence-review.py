#!/usr/bin/env python3
"""Create and run constrained Claude Code / GLM evidence-review shards."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from foxpro_memory.reviewer import create_review_plan, review_plan_status, run_claude_review_batch, run_claude_review_chunk


def cache_path(project):
    root=Path(os.environ.get('FOXPRO_MEMORY_CACHE',str(Path.home()/'.cache/foxpro-codebase-memory/indexes'))).expanduser()
    return root/(project+'.sqlite')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    plan=commands.add_parser('plan',help='Queue every indexed source unit and graph edge for review')
    plan.add_argument('--project',required=True)
    plan.add_argument('--question',required=True)
    plan.add_argument('--output-root',default=str(Path.home()/'.cache/foxpro-codebase-memory/reviews'))
    plan.add_argument('--max-source-chars',type=int,default=400000)
    plan.add_argument('--max-edges',type=int,default=5000)
    run=commands.add_parser('run',help='Review one queued shard through configured Claude Code / GLM')
    run.add_argument('--plan',required=True)
    run.add_argument('--chunk',required=True)
    run.add_argument('--model',default='glm-5.3-flash')
    run.add_argument('--max-budget-usd',type=float,default=0.25)
    run.add_argument('--timeout-seconds',type=int,default=900)
    status=commands.add_parser('status',help='Show every review shard and its local completion state')
    status.add_argument('--plan',required=True)
    batch=commands.add_parser('batch',help='Run a bounded number of pending review shards')
    batch.add_argument('--plan',required=True)
    batch.add_argument('--max-chunks',type=int,default=1)
    batch.add_argument('--model',default='glm-5.3-flash')
    batch.add_argument('--max-budget-usd',type=float,default=0.25)
    batch.add_argument('--timeout-seconds',type=int,default=900)
    args=parser.parse_args()
    try:
        if args.command=='plan':
            database=cache_path(args.project)
            if not database.is_file():raise ValueError('Project is not indexed: '+args.project)
            result=create_review_plan(database,args.project,args.question,args.output_root,args.max_source_chars,args.max_edges)
        elif args.command=='run':
            result=run_claude_review_chunk(args.plan,args.chunk,model=args.model,max_budget_usd=args.max_budget_usd,timeout_seconds=args.timeout_seconds)
        elif args.command=='status':
            result=review_plan_status(args.plan)
        else:
            result=run_claude_review_batch(args.plan,max_chunks=args.max_chunks,model=args.model,max_budget_usd=args.max_budget_usd,timeout_seconds=args.timeout_seconds)
    except Exception as exc:
        print(json.dumps({'error':str(exc)}))
        return 1
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

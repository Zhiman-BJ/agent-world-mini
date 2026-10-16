"""Independent CLI; never changes the repository's default verifier."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .application.runner import apply
from .data import load_selected, prepare_evidence, DEFAULT_DATA_ROOT, CASES
from .generation.pipeline import generate
from .validate_reference import run_batch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ['generate','apply','validate-reference']:
        p=sub.add_parser(command)
        p.add_argument('--config',type=Path)
        p.add_argument('--output',type=Path,required=True)
        if command=='apply':
            p.add_argument('--verifier',type=Path,required=True)
            p.add_argument('--evidence',type=Path,required=True)
        else:
            p.add_argument('--data-root',type=Path,default=DEFAULT_DATA_ROOT)
            p.add_argument('--cases',nargs='+',choices=list(CASES),default=list(CASES) if command=='validate-reference' else ['S21'])
    args=parser.parse_args()
    config=json.loads(args.config.read_text()) if args.config else {}
    if args.command=='apply': result=apply(args.verifier,args.evidence,args.output,config)
    elif args.command=='validate-reference': result=run_batch(args.data_root,args.output,args.cases,config)
    else:
        results=[]
        for name,case in load_selected(args.data_root,args.cases):
            out=args.output/case.task['task_id'];out.mkdir(parents=True,exist_ok=True)
            evidence=prepare_evidence(case,out/'source_evidence')
            results.append(generate(evidence,out,config))
        result=results
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()

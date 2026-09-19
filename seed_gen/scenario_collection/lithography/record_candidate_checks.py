"""Retain negative candidate evidence separately from accepted task reports."""
from pathlib import Path
import ast,hashlib,importlib.metadata,json,subprocess,sys

BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[2]
def main():
    source=ROOT/'seed_pypi_raw/lithography/lithosim/lithosim.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    anneal=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='anneal')
    predicates=[ast.unparse(n.test) for n in ast.walk(anneal) if isinstance(n,ast.If)]
    bad=next(p for p in predicates if '9.0 - nc * 0.1' in p)
    thresholds=[9.-nc*.1 for nc in range(9)]
    assert min(thresholds)>1
    commands=[['probe_differentiable.py'],['probe_snapshots.py','openilt']]
    records=[]
    for args in commands:
        completed=subprocess.run([sys.executable,str(BASE/args[0]),*args[1:]],cwd=ROOT,text=True,capture_output=True,timeout=90)
        records.append(dict(command=[sys.executable,(BASE/args[0]).relative_to(ROOT).as_posix(),*args[1:]],returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr))
        assert completed.returncode==0
    report=dict(status='research_evidence_only',source_kind='unreleased_commit_snapshot',lithosim_anneal=dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),predicate=bad,nc_range=[0,8],minimum_exit_threshold=min(thresholds),rng_random_range='[0,1)',conclusion='The while True selection loop cannot terminate; not executed.',upstream_modified=False),probes=records,boundary='These are excluded candidates, not the eight accepted stable-package tasks. OpenILT is a smoke check only; no independent full task oracle claimed.')
    (BASE/'research/candidate_checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (BASE/'runtime/requirements.freeze.txt').write_text('\n'.join(sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions()))+'\n',encoding='utf-8')
    print('Recorded excluded candidate evidence and final runtime freeze',flush=True)

if __name__=='__main__':main()

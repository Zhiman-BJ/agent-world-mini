"""Explicit S56/S71 rerun: keep original design/main and implement missing files."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil

from task_gen.test_verifier.program_verifier.generation.model import GenerationModel
from task_gen.test_verifier.program_verifier.generation.pipeline import generate, write_json
from task_gen.test_verifier.program_verifier.application.runner import apply
from task_gen.test_verifier.program_verifier.validate_reference import write_report, usage_totals

BASE = Path(__file__).resolve().parent
OLD = BASE/'runs/program_v1_20261015'
NEW = BASE/'runs/checker_fix_20261015'
CONFIG = json.loads((BASE/'program_verifier/run_config.json').read_text())
CONFIG['implementation_concurrency'] = 2


def run(row):
    name = row['case']
    original = Path(row['directory'])
    out = NEW/row['task_id']
    out.mkdir()
    logs = out/'generation'
    logs.mkdir()
    reuse = logs/'reused_design'
    shutil.copytree(original/'generation/01_design', reuse)
    (reuse/'call.json').unlink(missing_ok=True)
    write_json(reuse/'reuse.json', {'source': str(original/'generation/01_design'), 'new_model_call': False})
    evidence = original/'source_evidence'
    model = GenerationModel(evidence, CONFIG, logs)
    def call(prompt, *, stage, output_dir):
        if stage == 'design':
            workspace=reuse/'verifier'
            workspace.mkdir()
            for filename in ('rubrics.json','main.py'):
                shutil.copyfile(original/'verifier'/filename,workspace/filename)
            return workspace
        return model(prompt, stage=stage, output_dir=output_dir)
    print(f'{name}: retained design, implementing missing files', flush=True)
    g = generate(evidence, out, CONFIG, model_call=call)
    assert (out/'verifier/main.py').read_bytes() == (original/'verifier/main.py').read_bytes()
    a = None
    design = {}
    if g['status'] == 'generated':
        print(f'{name}: generated; applying', flush=True)
        design = json.loads((out/'verifier/rubrics.json').read_text())
        a = apply(out/'verifier', evidence, out/'reference_validation', CONFIG)
    usages = [json.loads(p.read_text()).get('usage') for p in logs.glob('*/call.json')]
    result = {'case':name,'task_id':row['task_id'],'directory':str(out),'generation':g,'application':a,'design':design,'generation_usage':usage_totals(usages),'reused_design_source':str(original),'evidence_source':str(evidence)}
    write_json(out/'rerun_record.json', result)
    print(f'{name}: {g["status"]}; application={a.get("mode") if a else None}; score={a.get("score") if a else None}', flush=True)
    return result


if __name__ == '__main__':
    NEW.mkdir(exist_ok=False)
    rows = json.loads((OLD/'summary.json').read_text())['cases']
    selected = [r for r in rows if r['case'] in ('S56','S71')]
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for row in pool.map(run, selected):
            results.append(row)
            write_report(NEW, results)
    with (NEW/'RESULTS.md').open('a') as report:
        report.write('\n本批次仅S56/S71，为用户明确要求后的重试；复用首轮设计和main.py，未重新拆分或修改标准，缺失实现由真实模型生成。生成成本仅包括本轮实现调用，首轮设计成本见原批次。原六例首轮统计保持不变。\n')

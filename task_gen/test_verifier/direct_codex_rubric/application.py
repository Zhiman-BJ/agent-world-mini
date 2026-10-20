"""Fixed-rubric scoring through the original Codex investigation runtime."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

from task_gen.task_eval_verifier import (
    PROMPT, _copy_state, run_verification_agent, serialized_state_preparation, verify_execution,
    validate_verdict,
)
from ..codex_tool_verifier.application import investigation_mounts, usage_events
from ..codex_tool_verifier.contracts import require
from ..program_verifier.contracts import aggregate_results, resolve_evidence_path
from ..program_verifier.environment_runtime import load_fallback, prepare_runtime_view, runtime_instructions
from ..program_verifier.generation.pipeline import write_json


def build_prompt(document, instructions, *, legacy_output=False):
    prompt = PROMPT.split('\n四、输出')[0]
    prompt = prompt.replace('给出有证据支持的 PASS 或 FAIL。', '按已固定 rubric 给出有证据支持的完成度评分。')
    prompt = prompt.replace('1. 从任务识别目标、条件、范围与交付要求，分配 R1、R2 等编号并保留对应原文。',
                            '1. 阅读下方已固定 rubric，按其原有编号核查目标、条件、范围与交付要求。')
    document = dict(document, rubrics=[{k:v for k,v in r.items() if k not in
                                    ('method','static_tools','required_tools')} for r in document['rubrics']])
    prompt += '''
四、固定 rubric 与评分
下方 rubric 是本任务预先固定的评分标准，已包含评分目标、权重、档位和缺失政策。
不得增删 rubric、改变编号、权重或评分标准。调查方法由你自主选择，逐项解释事实如何对应评分档位。
共享定义、容差及说明原文保留。全部 rubric 由你独立调查并依据固定标准评分。
本次不提供预生成的静态检查工具，不读取其他验证运行的代码、调查记录或评分。不调用其他模型。
直接检查出现可诊断的命令或脚本错误时，可修正自己的检查继续调查；不能修改被测产物或评分标准。
若最终无法可靠判断或环境无法继续调查，返回 {"verification_error":"具体原因"}，由宿主整任务回退。
任务完成程度与验证器执行故障不同：零分是有效评分，不触发回退。每个 rubric 必须恰好返回一次。
完成 scratch/investigation.md 后，最终仅返回以下 JSON，不自行计算总分：
{"task_id":"与 request 一致","outcome":"pass 或 fail","summary":"决定性依据","rubrics":[{"rubric_id":"R1","score":1,
"reason":"依照固定标准的评分依据","evidence":[{"path":"evidence/actual/answer.txt",
"locator":"行号或记录键","finding":"亲自观察的事实"}]}]}
outcome 由你依据原任务和核实事实判断：核心目标未完成或存在实质错误为 fail；不影响任务完成的局部缺陷可为 pass。summary 说明决定性依据。
整体判定与逐项分数同时提交；不得自行设总分门槛，也不得用整体判定更改固定 rubric 分数。
binary 分数只能为整数 0/1；ordinal_5 为整数 0..5。每项至少一个真实证据。
证据路径是调查目录下 evidence/ 或 scratch/ 中已有文件或目录的相对路径。
证明缺失时在 scratch 保存目录清单或查询结果并引用，不能引用不存在的产物。
原有验收边界用于查明事实；具体得分始终按固定 rubric，不能以整体判断替换逐项评分。
成本：只读取判断所需材料，优先针对文件、记录、页面定位，避免全库或全目录反复扫描。

Direct 专属必要性与核验流程（生成要求仅用于理解固定标准，应用时不得改写标准）：
''' + (Path(__file__).parent/'prompts/application_contract.md').read_text() + '''

固定 rubric JSON：
''' + json.dumps(document, ensure_ascii=False, indent=2) + '\n共享验收说明：\n' + instructions + (
        '\n本方法最终输出契约优先于共享说明中的输出格式：task_id、outcome、summary 和 rubrics 都是必需字段。')
    if legacy_output:
        prompt += '''
\n原 verifier API 兼容输出（补充上述 JSON，不替代 rubrics）：
同时返回 requirements、non_blocking_issues、failure_causes，结构沿用原接口：
"requirements":[{"id":"R1","task_quote":"任务原文连续子串","requirement":"任务要求",
"analysis":"核实事实及满足情况","evidence":[{"path":"evidence/actual/answer.txt",
"locator":"位置","finding":"观察事实"}],"passed":true}],
"non_blocking_issues":[],"failure_causes":[]。
requirements 记录原任务的必要完成要求及真实满足情况，编号须在调查记录中出现；
不能把非必要的质量改进列为未通过的必要要求，不能按 rubric 分数推断 passed。
必要子项的实质失败须在 requirements 和整体 outcome 中一致反映。
outcome 为 pass 当且仅当所有 requirements 的 passed 为 true；fail 时 failure_causes 非空。
rubrics 仍须按固定标准独立评分，保留部分得分。所有字段由本次真实调查产生。
'''
    return prompt


def validate_result(value, root, document, *, legacy_output=False):
    require(isinstance(value,dict) and not value.get('verification_error'), str(value))
    request=json.loads((root/'request.json').read_text())
    require(value.get('task_id')==request['task_id'], 'task_id mismatch')
    require(value.get('outcome') in ('pass','fail'), 'outcome must be pass or fail')
    require(isinstance(value.get('summary'),str) and value['summary'].strip(), 'summary missing')
    investigation=root/'scratch/investigation.md'
    require(investigation.is_file() and investigation.resolve().is_relative_to((root/'scratch').resolve()),
            'Missing scratch/investigation.md')
    record=investigation.read_text()
    require(bool(record.strip()), 'Empty investigation record')
    aggregated=aggregate_results(document['rubrics'],value.get('rubrics'))
    for result in aggregated['rubrics']:
        require(result['rubric_id'] in record, 'Investigation does not cover '+result['rubric_id'])
        for ref in result['evidence']:
            path=Path(ref['path'])
            require(not path.is_absolute() and path.parts and path.parts[0] in ('evidence','scratch'),
                    'Evidence must use an evidence/ or scratch/ relative path')
            resolved=resolve_evidence_path(str(path),root,root/'scratch')
            require(any(resolved.is_relative_to((root/p).resolve()) for p in ('evidence','scratch')),
                    'Evidence must name a file or directory inside evidence or scratch')
    result = dict(task_id=value['task_id'],outcome=value['outcome'],summary=value['summary'],**aggregated)
    if legacy_output:
        legacy = validate_verdict({k: value[k] for k in (
            'task_id', 'requirements', 'non_blocking_issues', 'summary', 'failure_causes', 'outcome')}, root)
        result.update(legacy)
    return result


def investigate_prepared(verifier_dir, evidence_dir, root, config=None):
    """Investigate a prepared original-API workspace without relocating its logs."""
    verifier, evidence, root = (Path(p).resolve() for p in (verifier_dir, evidence_dir, root))
    config = dict(config or {})
    document = json.loads((verifier/'rubrics.json').read_text())
    instructions = (verifier/'instructions.md').read_text()
    legacy_output = config.get('legacy_output', False)
    cfg = dict(config.get('codex', {}))
    if config.get('readonly_paths'):
        cfg['readonly_paths'] = config['readonly_paths']
    cfg.setdefault('attempts', 1)
    cfg['timeout_seconds'] = int(config.get('application_timeout_seconds', cfg.get('timeout_seconds', 1800)))
    if config.get('codex_home'):
        cfg.setdefault('codex_home', config['codex_home'])
    prepare_runtime_view(root, evidence, config)
    (root/'prompt.txt').write_text(build_prompt(document, instructions, legacy_output=legacy_output)
        +'\n调用原环境工具时先 cd scratch；下方命令相对于该目录。\n'
        +runtime_instructions(evidence, root/'scratch', config))
    cfg['runtime_paths'] = list(dict.fromkeys([str(evidence), *cfg.get('runtime_paths', []),
                               *config.get('runtime_paths', []),
                               *map(str, investigation_mounts(evidence, config))]))
    return run_verification_agent(root, cfg, result_validator=lambda value, path:
        validate_result(value, path, document, legacy_output=legacy_output))


def apply(verifier_dir,evidence_dir,output_dir,config=None):
    verifier,evidence,output=(Path(p).resolve() for p in (verifier_dir,evidence_dir,output_dir))
    config=dict(config or {})
    require(not any(output.is_relative_to(p) or p.is_relative_to(output) for p in (verifier,evidence)),
            'Output must be separate from frozen verifier and evidence')
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    root=output/'investigation'
    cfg=dict(config.get('codex',{}))
    cfg.setdefault('attempts',1)
    cfg['timeout_seconds']=int(config.get('application_timeout_seconds',cfg.get('timeout_seconds',1800)))
    if config.get('codex_home'):
        cfg.setdefault('codex_home',config['codex_home'])
    failure=None
    try:
        with serialized_state_preparation():
            root.mkdir()
            _copy_state(evidence/'evidence',root/'evidence')
            shutil.copyfile(evidence/'request.json',root/'request.json')
            (root/'scratch').mkdir()
        value=investigate_prepared(verifier,evidence,root,config)
        result=dict(mode='direct_codex_rubric',status='completed',**value)
    except Exception as error:
        failure={'type':type(error).__name__,'message':str(error)}
        write_json(output/'verification_error.json',failure)
    main_seconds=time.monotonic()-started
    cost={'calls':[{'method':'codex','model':cfg.get('model','unknown'), 'seconds':main_seconds,
                    'usage':usage_events(root/'logs'),'status':'error' if failure else 'completed'}],
          'tools_seconds':0}
    if failure and config.get('fallback_enabled', True) is False:
        result=dict(mode='direct_codex_rubric',status='verification_error',score=None,error=failure)
    elif failure:
        fallback_started=time.monotonic()
        try:
            inputs=load_fallback(evidence)
            fallback_config=dict(config.get('codex',{}))
            fallback_config['method']='codex'
            if config.get('codex_home'):fallback_config.setdefault('codex_home',config['codex_home'])
            verdict=verify_execution(run_dir=output/'fallback',config=fallback_config,**inputs)
            result=dict(mode='fallback_codex',status='completed',score=None,outcome=verdict['outcome'],verdict=verdict)
        except Exception as error:
            result=dict(mode='fallback_codex',status='verification_error',score=None,
                        fallback_error={'type':type(error).__name__,'message':str(error)})
        result['fallback_reason']=failure
        cost['fallback']={'seconds':time.monotonic()-fallback_started,'usage':usage_events(output/'fallback/logs')}
    result.update(seconds=time.monotonic()-started,cost=cost,static_tools_enabled=False,tool_calls=[])
    write_json(output/'result.json',result)
    return result

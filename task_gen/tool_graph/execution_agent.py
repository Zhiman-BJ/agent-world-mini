"""Step 3: single session per attempt; execute only the deficit of candidates."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime
import json
from pathlib import Path
import secrets
import shutil
import time

from .llm import InferenceResult, _TRACE_CONTEXT, _record_call, parse_json_object
from .review_agent import _ReviewClient
from .step_3_chain_execute import _tools, _workspace_signature, _schema_error


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _context(candidate, environment):
    return {'objective': candidate['objective'], 'original_chain': candidate['chain'],
            'design_basis': candidate.get('design_basis'),
            'tools': [{k: t[k] for k in ('name', 'description', 'inputSchema', 'outputSchema', 'usageConditions') if k in t}
                      for t in environment['tools']],
            'environment': {k: environment.get(k) for k in ('name', 'description', 'summary', 'record_sets', 'relationships', 'filesystem_scopes')}}


def execution_prompt(candidate, environment, *, enable_web_search=False):
    prompt = (Path(__file__).parent / 'prompts/step3_single.txt').read_text()
    if not enable_web_search:
        start, end = prompt.index('第二步：'), prompt.index('第三步：')
        prompt = prompt[:start] + '第二步：本次关闭网络参考，禁止外部访问。\n\n' + prompt[end:]
    return prompt + '\n' + json.dumps(_context(candidate, environment), ensure_ascii=False, separators=(',', ':'))


def meaningful_calls(records, tools):
    """Structural filter only; the agent must judge actual business contribution."""
    calls = []
    for record in records:
        if record['tool'] not in tools or _schema_error(tools[record['tool']]['inputSchema'], record['arguments']):
            continue
        result = record.get('result')
        if isinstance(result, dict) and (record.get('error') is None or (result.get('success') is False
                and _schema_error(tools[record['tool']]['outputSchema'], result) is None)):
            calls.append(record)
    return calls


def _parse_output(text, phase):
    value = parse_json_object(text)
    expected = {'objective', 'reason', 'completed', 'answer', 'score'}
    if phase == 'review':
        expected |= {'call_indices', 'chain_changes', 'restart'}
    if set(value) != expected:
        raise ValueError(f'输出字段必须为 {sorted(expected)}')
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ('objective', 'reason', 'answer')):
        raise ValueError('objective/reason/answer 必须为非空文本')
    if type(value['completed']) is not bool:
        raise ValueError('completed 必须为布尔值')
    if type(value['score']) is not int or value['score'] not in range(6):
        raise ValueError('score 必须为0–5整数')
    if phase == 'review':
        if not isinstance(value['chain_changes'], str) or not value['chain_changes'].strip():
            raise ValueError('chain_changes 必须说明修改方案及依据，或不修改的依据')
        if type(value['restart']) is not bool:
            raise ValueError('restart 必须为布尔值')
    return value


def selected_calls(payload, records, tools):
    indices = payload.get('call_indices')
    if not isinstance(indices, list) or any(type(i) is not int or not 1 <= i <= len(records) for i in indices):
        raise ValueError(f'call_indices 必须引用真实调用编号1到{len(records)}')
    if indices != sorted(set(indices)):
        raise ValueError('call_indices 必须按实际调用顺序排列且不能重复')
    calls = [records[i - 1] for i in indices]
    invalid = [f"{i}:{records[i - 1]['tool']}" for i in indices
               if not meaningful_calls([records[i - 1]], tools)]
    if invalid:
        raise ValueError('以下编号不是合法环境业务调用（辅助工具、参数错误或服务失败），不能计入call_indices：'
                         + ', '.join(invalid) + '。其余有效负结果不因此被排除。')
    return calls


def review_prompt(records, tools):
    prompt = (Path(__file__).parent / 'prompts/step3_review.txt').read_text()
    return prompt + '\n本会话真实调用编号（完整结果见会话中的工具回复）：\n' + json.dumps(
        [{'index': i, 'tool': r['tool'], 'arguments': r['arguments'], 'error': r.get('error'),
          'eligible_business_call': bool(meaningful_calls([r], tools))}
         for i, r in enumerate(records, 1)], ensure_ascii=False, separators=(',', ':'))


class _Session:
    """One persistent Codex thread and state copy; retries never silently start a new thread."""

    def __init__(self, root, source, environment, tools, config, phase, index, runtime=None):
        self.root, self.config, self.phase, self.index = root, config, phase, index
        self.tools = tools
        root.mkdir(parents=True)
        shutil.copytree(source, root / 'final')
        (root / 'agent').mkdir()
        server = {'environment': environment, 'tools': list(tools.values()),
                  'workspace': str(root / 'final'), 'trace': str(root / 'tool_calls.jsonl'), 'resume_trace': True,
                  'max_tool_calls': config.execution.get('agent_max_tool_calls', 100),
                  'timeout': config.execution.get('tool_timeout_seconds', 300),
                  'memory_limit': config.execution.get('tool_max_memory_bytes', 2 * 1024**3),
                  'write_limit': config.execution.get('tool_max_write_bytes', 256 * 1024**2),
                  'software_root': config.execution.get('tool_software_root'),
                  'software': (runtime or {}).get('software')}
        if runtime and runtime.get('runtime'):
            server['runtime'] = runtime['runtime']
        server['review_choice_seed'] = config.llm.get('review_choice_seed', secrets.randbits(64)) + index
        _write(root / 'server.json', server)
        self.budget = int(config.llm.get('timeout_seconds', 1800))
        self.elapsed, self.turn = 0.0, 0
        self.last_usage = {}
        self.client = _ReviewClient(server_config=root / 'server.json', model=config.llm.get('model'),
            codex_home=config.llm.get('codex_home'), reasoning_effort=config.llm.get('reasoning_effort'),
            timeout_seconds=self.budget, enable_web_search=config.execution.get('enable_web_search', True),
            sandbox='read-only', log_directory=root / 'logs', persistent_session=True)

    @property
    def remaining_seconds(self):
        return max(0, int(self.budget - self.elapsed))

    def ask(self, prompt):
        for attempt in range(self.config.execution.get('retry_count', 3) + 1):
            if self.turn and not self.client.session_id:
                raise ValueError('会话ID缺失，不能在已有状态上另起会话')
            remaining = self.remaining_seconds
            if remaining <= 0:
                raise TimeoutError('本会话累计运行时间超限')
            self.turn += 1
            self.client.timeout_seconds = remaining
            started, started_at = time.perf_counter(), datetime.now().astimezone().isoformat()
            result, failure, text = None, None, ''
            _write(self.root / f'turn_{self.turn:02d}_request.json', {'prompt': prompt, 'session_id': self.client.session_id})
            try:
                text = self.client.run(prompt, working_directory=self.root / 'agent')
                result = InferenceResult(text, {}, self.config.llm.get('model'))
                if not self.client.session_id:
                    raise ValueError('没有取得可续接的会话ID')
                payload = _parse_output(text, self.phase)
                if self.phase == 'review':
                    selected_calls(payload, _records(self.root / 'tool_calls.jsonl'), self.tools)
            except Exception as error:
                failure = error
            finally:
                self.elapsed += time.perf_counter() - started
                logdir = self.client.last_log_directory
                logs = {name: (logdir / f'{name}.log').read_text(errors='replace') for name in ('stdout', 'stderr')
                        if logdir is not None and (logdir / f'{name}.log').exists()}
                events = []
                for line in logs.get('stdout', '').splitlines():
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except json.JSONDecodeError:
                        pass
                cumulative_usage = next((e.get('usage', {}) for e in reversed(events) if e.get('type') == 'turn.completed'), {})
                # Persistent Codex threads emit lifetime totals, not this turn's delta.
                usage = {k: v - self.last_usage.get(k, 0) if v >= self.last_usage.get(k, 0) else v
                         for k, v in cumulative_usage.items() if isinstance(v, (int, float))}
                if cumulative_usage:
                    self.last_usage = cumulative_usage
                if result is not None:
                    result = InferenceResult(text, usage, result.model)
                _write(self.root / f'turn_{self.turn:02d}_result.json',
                       {'answer': text, 'error': str(failure) if failure else None, 'session_id': self.client.session_id})
                _record_call(_TRACE_CONTEXT.get(), index=self.index, prompt=prompt, system_prompt=None, history=(),
                    backend='codex-execution', model=self.config.llm.get('model'), started_at=started_at, started=started,
                    result=result, error=failure, agent_log={**logs, 'events': events, 'phase': self.phase,
                    'session_id': self.client.session_id, 'directory': str(self.root),
                    'cumulative_usage': cumulative_usage, 'usage_scope': 'turn_delta',
                    'tool_calls': _records(self.root / 'tool_calls.jsonl')})
            if failure is None:
                return payload
            if attempt == self.config.execution.get('retry_count', 3) or not self.client.session_id:
                raise failure
            prompt = f'上次调用或输出发生错误：{failure}。根据保留的会话与实际状态继续处理并按原定结构返回。'
            if self.phase == 'review':
                prompt += '\n' + review_prompt(_records(self.root / 'tool_calls.jsonl'), self.tools)


def execute_candidates(stage_input):
    config, environment = stage_input['config'], stage_input['environment']
    tools = _tools(environment)
    runtime = stage_input.get('runtime', {})
    source = Path(runtime.get('initial_state', config.environment_dir / 'state')).resolve()
    signature = _workspace_signature(source)
    run_dir = stage_input['run_dir'].resolve()
    tasks_root = run_dir / 'tasks'
    tasks_root.mkdir(parents=True, exist_ok=True)
    candidates = stage_input['tasks']
    settings = {k: config.execution.get(k, v) for k, v in
                (('target_tool_calls', 20), ('min_tool_calls', 11), ('max_concurrency', 4), ('retry_count', 3))}
    keep = config.planning.get('keep_top_count', 10)
    if type(keep) is not int or keep < 1:
        raise ValueError('keep_top_count 必须为正整数')
    if any(type(v) is not int or v < (0 if k == 'retry_count' else 1) for k, v in settings.items()):
        raise ValueError('长度、并发必须为正整数，重试次数必须为非负整数')
    if settings['min_tool_calls'] < 11 or settings['target_tool_calls'] < settings['min_tool_calls']:
        raise ValueError('接收下限至少11次，目标长度不能小于接收下限')
    ids = [c['task_id'] for c in candidates]
    if any(not isinstance(i, str) or not i or Path(i).name != i or i in ('.', '..') for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('非法或重复 task_id')
    if any((tasks_root / i).exists() for i in ids):
        raise ValueError('任务目录已存在；请使用新的运行目录，已有结果保持不动')

    def run(candidate):
        root = tasks_root / candidate['task_id']
        root.mkdir()
        shutil.copytree(source, root / 'initial')
        worker, payload, attempts, calls = None, {}, [], []
        accepted, failure = False, None
        remaining = int(config.llm.get('timeout_seconds', 1800))
        try:
            for round_index in range(2):
                if remaining <= 0:
                    raise TimeoutError('任务累计运行时间已耗尽，不能重新执行')
                restart = worker is None or payload.get('restart', False)
                if restart:
                    calls = []
                    worker = _Session(root / 'rounds' / f'{round_index + 1:02d}', root / 'initial',
                                      environment, tools, config, 'execution', ids.index(candidate['task_id']), runtime)
                    worker.budget = remaining
                worker.phase = 'execution'
                prompt = execution_prompt(candidate, environment,
                    enable_web_search=config.execution.get('enable_web_search', True)) if restart else ''
                for key in ('target_tool_calls', 'min_tool_calls'):
                    prompt = prompt.replace('{' + key + '}', str(settings[key]))
                if round_index:
                    prompt += '\n本次是唯一一次重试。' + ('按你的判断从原始初态重新开始。' if restart else '按你的判断续接当前会话和状态。')
                    prompt += '\n上一轮完整目标：' + payload['objective'] + '\n修改方案和判断依据：' + payload['chain_changes'] + '\n' + payload['reason']
                    prompt += f"\n上一轮确认的有效业务调用数为{attempts[-1]['calls']}。"
                    if payload['completed']:
                        prompt += '对照原链和design_basis，寻找尚未充分融合且能服务于同一目标的有效工作。不能预设短链必然漏做，原链可能冗余，环境也可能限制深度。'
                    else:
                        prompt += '按上一轮复核明确的修改方案处理阻断，不能降低交付要求宣告成功。'
                    prompt += f"此次不再重试，至少{settings['min_tool_calls']}次且完整完成才可接收。返回objective、reason、completed、answer、score五个字段。"
                attempts.append({'round': round_index + 1, 'restart': restart, 'directory': str(worker.root.relative_to(run_dir))})
                payload = {}
                payload = worker.ask(prompt)
                _write(worker.root / f'attempt_{round_index + 1:02d}_draft.json', payload)
                worker.phase = 'review'
                payload = worker.ask(review_prompt(_records(worker.root / 'tool_calls.jsonl'), tools))
                remaining = worker.remaining_seconds
                records = _records(worker.root / 'tool_calls.jsonl')
                calls = selected_calls(payload, records, tools)
                attempts[-1].update(completed=payload['completed'], calls=len(calls),
                    objective=payload['objective'], session_id=worker.client.session_id,
                    remaining_seconds=remaining, call_indices=payload['call_indices'],
                    chain_changes=payload['chain_changes'])
                _write(worker.root / 'result.json', payload)
                _write(worker.root / f'attempt_{round_index + 1:02d}_review.json', payload)
                if not payload['completed']:
                    if round_index == 0 and payload['restart']:
                        continue
                    failure = '执行者未完整完成目标：' + payload['reason']
                    break
                if round_index == 0 and len(calls) < settings['target_tool_calls']:
                    continue
                accepted = len(calls) >= settings['min_tool_calls']
                if not accepted:
                    failure = f"有效调用数{len(calls)}低于接收下限{settings['min_tool_calls']}"
                break
        except Exception as error:
            failure = str(error)
        if _workspace_signature(source) != signature:
            accepted, failure = False, '源初态被修改'
        records = _records(worker.root / 'tool_calls.jsonl') if worker else []
        reason = '\n\n'.join(s for s in (payload.get('reason', ''), failure) if s)
        final = worker.root / 'final' if worker else root / 'initial'
        output = {**candidate, 'chain': [c['tool'] for c in calls],
                  'objective': payload.get('objective', candidate['objective']),
                  'logic_score': payload.get('score', 0) if accepted else 0, 'logic_reason': reason,
                  'llm_review': {'original_chain': candidate['chain'], 'original_objective': candidate['objective'],
                                'reason': reason, 'error': failure},
                  'execution': {'success': accepted, 'tool_calls': calls, 'raw_tool_calls': records,
                    'call_indices': payload.get('call_indices', []), 'chain_changes': payload.get('chain_changes', ''),
                    'initial_state': str((root / 'initial').relative_to(run_dir)),
                    'final_state': str(final.relative_to(run_dir)), 'answer': payload.get('answer', ''),
                    'error': failure, 'attempts': attempts}}
        _write(root / 'agent_result.json', output)
        print(candidate['task_id'], 'completed=', accepted, 'calls=', len(calls), flush=True)
        return output

    output, batches, cursor, accepted_count = [], [], 0, 0
    progress_path = run_dir / 'step3_progress.json'
    def progress(status):
        _write(progress_path, {'status': status, 'target': keep, 'accepted': accepted_count,
            'remaining_needed': max(0, keep - accepted_count), 'batches': batches,
            'processed': [t['task_id'] for t in output], 'pending': ids[cursor:]})
    with ThreadPoolExecutor(max_workers=settings['max_concurrency']) as pool:
        while accepted_count < keep and cursor < len(candidates):
            batch = candidates[cursor:cursor + keep - accepted_count]
            cursor += len(batch)
            batches.append({'task_ids': [c['task_id'] for c in batch]})
            progress('running')
            futures = [pool.submit(copy_context().run, run, candidate) for candidate in batch]
            results = [f.result() for f in futures]
            output.extend(results)
            accepted_count += sum(t['execution']['success'] for t in results)
            progress('running')
    progress('complete' if accepted_count >= keep else 'pool_exhausted')
    return {'tasks': output}

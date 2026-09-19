"""Step 3: parent plans/reviews; isolated executor sessions produce the final chain."""
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
from .step_3_chain_execute import _tools, _workspace_signature, _schema_error, _bounded_calls
from .step_2_chain_sample import _select_final_chains


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
    return (Path(__file__).parent / 'prompts/step3_executor.txt').read_text() + '\n' + json.dumps(
        _context(candidate, environment), ensure_ascii=False, separators=(',', ':'))


def meaningful_calls(records, tools):
    """Structural filter only; the parent judges actual business contribution."""
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
    expected = {'action', 'objective', 'reason', 'feedback', 'score'} if phase == 'preparation' else {'reason', 'completed', 'answer'}
    if set(value) != expected:
        raise ValueError(f'输出字段必须为 {sorted(expected)}')
    for key in ('reason', 'objective') if phase == 'preparation' else ('reason', 'answer'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f'{key} 必须是非空文本')
    if phase == 'preparation':
        if value['action'] not in ('execute', 'repair', 'accept', 'reject'):
            raise ValueError('非法 action')
        if type(value['score']) is not int or value['score'] not in range(6):
            raise ValueError('score 必须为0–5整数')
        if value['action'] != 'accept' and value['score'] != 0:
            raise ValueError('只有 accept 可以给出非零评分')
        if not isinstance(value['feedback'], str) or bool(value['feedback'].strip()) != (value['action'] == 'repair'):
            raise ValueError('仅 repair 必须填写非空 feedback')
    elif type(value['completed']) is not bool:
        raise ValueError('completed 必须为布尔值')
    return value


class _Session:
    """One persistent Codex thread and state copy; retries never silently start a new thread."""

    def __init__(self, root, source, environment, tools, config, phase, index, runtime=None):
        self.root, self.config, self.phase, self.index = root, config, phase, index
        root.mkdir(parents=True)
        shutil.copytree(source, root / 'final')
        (root / 'agent').mkdir()
        declaration = environment
        if phase == 'preparation':
            declaration = {**environment, **{key: [{**item, 'access': 'read_only'} for item in environment.get(key, [])]
                                           for key in ('record_sets', 'filesystem_scopes')}}
        server = {'environment': declaration, 'tools': list(tools.values()),
                  'workspace': str(root / 'final'), 'trace': str(root / 'tool_calls.jsonl'), 'resume_trace': True,
                  'max_tool_calls': config.execution.get('agent_max_tool_calls', 100),
                  'timeout': config.execution.get('tool_timeout_seconds', 300),
                  'memory_limit': config.execution.get('tool_max_memory_bytes', 2 * 1024**3),
                  'write_limit': config.execution.get('tool_max_write_bytes', 256 * 1024**2),
                  'software_root': config.execution.get('tool_software_root'),
                  'software': (runtime or {}).get('software')}
        if phase == 'preparation':
            server['review_choice_seed'] = config.llm.get('review_choice_seed', secrets.randbits(64)) + index
        _write(root / 'server.json', server)
        self.budget = int(config.llm.get('timeout_seconds', 1800))
        self.elapsed, self.turn = 0.0, 0
        self.client = _ReviewClient(server_config=root / 'server.json', model=config.llm.get('model'),
            codex_home=config.llm.get('codex_home'), reasoning_effort=config.llm.get('reasoning_effort'),
            timeout_seconds=self.budget, enable_web_search=phase == 'preparation' and config.execution.get('enable_web_search', True),
            sandbox='read-only', log_directory=root / 'logs', persistent_session=True)

    def ask(self, prompt):
        for attempt in range(self.config.execution.get('retry_count', 3) + 1):
            if self.turn and not self.client.session_id:
                raise ValueError('会话ID缺失，不能在已有状态上另起会话')
            remaining = int(self.budget - self.elapsed)
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
                usage = next((e.get('usage', {}) for e in reversed(events) if e.get('type') == 'turn.completed'), {})
                if result is not None:
                    result = InferenceResult(text, usage, result.model)
                _write(self.root / f'turn_{self.turn:02d}_result.json',
                       {'answer': text, 'error': str(failure) if failure else None, 'session_id': self.client.session_id})
                _record_call(_TRACE_CONTEXT.get(), index=self.index, prompt=prompt, system_prompt=None, history=(),
                    backend='codex-execution', model=self.config.llm.get('model'), started_at=started_at, started=started,
                    result=result, error=failure, agent_log={**logs, 'events': events, 'phase': self.phase,
                    'session_id': self.client.session_id, 'directory': str(self.root), 'tool_calls': _records(self.root / 'tool_calls.jsonl')})
            if failure is None:
                return payload
            if attempt == self.config.execution.get('retry_count', 3) or not self.client.session_id:
                raise failure
            prompt = f'上次调用或输出发生错误：{failure}。根据保留的会话与实际状态继续处理并按原定结构返回；不要重放已经完成的写操作。'


def execute_candidates(stage_input):
    config, environment = stage_input['config'], stage_input['environment']
    tools = _tools(environment)
    runtime = stage_input.get('runtime', {})
    source = Path(runtime.get('initial_state', config.environment_dir / 'state')).resolve()
    signature = _workspace_signature(source)
    tasks_root = stage_input['run_dir'].resolve() / 'tasks'
    candidates = stage_input['tasks']
    if not candidates:
        return {'tasks': []}
    settings = {k: config.execution.get(k, v) for k, v in
                (('max_rounds', 3), ('target_tool_calls', 20), ('min_tool_calls', 10), ('max_concurrency', 4), ('retry_count', 3))}
    if any(type(v) is not int or v < (0 if k == 'retry_count' else 1) for k, v in settings.items()):
        raise ValueError('轮次、长度、并发必须为正整数，重试次数必须为非负整数')
    if settings['target_tool_calls'] < settings['min_tool_calls']:
        raise ValueError('目标长度不能小于接收下限')
    ids = [c['task_id'] for c in candidates]
    if any(not isinstance(i, str) or not i or Path(i).name != i or i in ('.', '..') for i in ids) or len(ids) != len(set(ids)):
        raise ValueError('非法或重复 task_id')
    if any((tasks_root / i).exists() for i in ids):
        raise ValueError('任务目录已存在')
    main_prompt = (Path(__file__).parent / 'prompts/step3_main.txt').read_text()
    for key, value in settings.items():
        main_prompt = main_prompt.replace('{' + key + '}', str(value))
    if not config.execution.get('enable_web_search', True):
        start, end = main_prompt.index('第二步：'), main_prompt.index('第三步：')
        main_prompt = main_prompt[:start] + '第二步：本次关闭网络参考，禁止外部访问。\n\n' + main_prompt[end:]
        main_prompt = main_prompt.replace('外部访问仅限内置网络搜索。', '禁止外部访问。')

    def run(candidate):
        root = tasks_root / candidate['task_id']
        root.mkdir(parents=True)
        shutil.copytree(source, root / 'initial')
        parent, worker, payload = None, None, {}
        attempts, decisions = [], []
        objective, accepted, failure, invalid = candidate['objective'], False, None, 0
        decision = {'reason': '', 'score': 0}
        try:
            parent = _Session(root / 'preparation', root / 'initial', environment, tools, config, 'preparation', ids.index(candidate['task_id']), runtime)
            prompt = main_prompt + '\n' + json.dumps(_context(candidate, environment), ensure_ascii=False, separators=(',', ':'))
            while True:
                decision = parent.ask(prompt)
                decisions.append(decision)
                _write(root / 'decisions.json', decisions)
                action, error = decision['action'], None
                calls = meaningful_calls(_records(worker.root / 'tool_calls.jsonl'), tools) if worker else []
                if action == 'execute' and len(attempts) >= settings['max_rounds']:
                    error = '完整执行轮次已用完，只能修复本轮、接收或拒绝'
                elif action in ('accept', 'repair'):
                    if worker is None or decision['objective'] != objective:
                        error = '接收或修复需要已有执行轮且目标保持不变；改变目标必须 execute'
                    elif action == 'accept' and (payload.get('completed') is not True or payload.get('error') or len(calls) < settings['min_tool_calls']):
                        error = '本轮未完整交付、运行异常或有效调用不足接收下限'
                    elif action == 'repair' and (not worker.client.session_id or worker.elapsed >= worker.budget):
                        error = '本轮会话无法继续，不能 repair'
                if error:
                    invalid += 1
                    if invalid > settings['retry_count']:
                        raise ValueError(error)
                    prompt = '决策无法执行：' + error + '。请根据现有结果重新判断。'
                    continue
                invalid = 0
                if action == 'reject':
                    failure = decision['reason']
                    break
                if action == 'accept':
                    accepted = True
                    break
                if action == 'execute':
                    objective = decision['objective']
                    worker = _Session(root / 'rounds' / f'{len(attempts) + 1:02d}', root / 'initial', environment, tools, config, 'execution', ids.index(candidate['task_id']), runtime)
                    attempts.append({'round': len(attempts) + 1, 'objective': objective, 'directory': str(worker.root.relative_to(stage_input['run_dir'].resolve()))})
                    worker_prompt = execution_prompt({**candidate, 'objective': objective}, environment)
                else:
                    worker_prompt = '请在本轮原目标下处理以下复核反馈，补查所需事实，修复并核验后返回原定JSON：\n' + decision['feedback']
                try:
                    payload = worker.ask(worker_prompt)
                except Exception as error:
                    payload = {'completed': False, 'reason': '', 'answer': '', 'error': str(error)}
                records = _records(worker.root / 'tool_calls.jsonl')
                attempts[-1].update(completed=payload['completed'], error=payload.get('error'),
                                    calls=len(meaningful_calls(records, tools)), session_id=worker.client.session_id)
                _write(worker.root / 'result.json', payload)
                prompt = '以下是本轮实际执行结果，请按第四至第六步复核并决定下一步。被裁剪内容不是完整证据，有缺口应要求本轮执行者补充核验。\n' + json.dumps(
                    {'objective': objective, 'round': len(attempts), 'remaining_rounds': settings['max_rounds'] - len(attempts),
                     'valid_call_count': attempts[-1]['calls'], 'result': payload,
                     'tool_calls': _bounded_calls(records, config.execution.get('tool_result_max_bytes', 65536))},
                    ensure_ascii=False, separators=(',', ':'))
        except Exception as error:
            failure = str(error)
        if _workspace_signature(source) != signature or (parent and _workspace_signature(parent.root / 'final') != signature):
            accepted, failure = False, '源初态或前置探索状态被修改'
        records = _records(worker.root / 'tool_calls.jsonl') if worker else []
        calls = meaningful_calls(records, tools)
        reason = '\n\n'.join(s for s in (decision.get('reason', ''), payload.get('reason', ''), failure) if s)
        final = worker.root / 'final' if worker else root / 'initial'
        output = {**candidate, 'chain': [c['tool'] for c in calls], 'objective': objective,
                  'logic_score': decision.get('score', 0) if accepted else 0, 'logic_reason': reason,
                  'llm_review': {'original_chain': candidate['chain'], 'original_objective': candidate['objective'], 'reason': reason, 'error': failure},
                  'execution': {'success': accepted, 'tool_calls': calls, 'raw_tool_calls': records,
                    'initial_state': str((root / 'initial').relative_to(stage_input['run_dir'].resolve())),
                    'final_state': str(final.relative_to(stage_input['run_dir'].resolve())),
                    'answer': payload.get('answer', ''), 'error': failure, 'attempts': attempts}}
        _write(root / 'agent_result.json', output)
        print(candidate['task_id'], 'completed=', accepted, 'calls=', len(calls), flush=True)
        return output

    with ThreadPoolExecutor(max_workers=min(settings['max_concurrency'], len(candidates))) as pool:
        futures = [pool.submit(copy_context().run, run, candidate) for candidate in candidates]
        output = [f.result() for f in futures]
    selected = _select_final_chains([t for t in output if t['execution']['success']],
        config.planning.get('keep_top_count', 10), config.planning.get('diversity_lambda', 10.0))
    selected_ids = {t['task_id'] for t in selected}
    return {'tasks': [t for t in output if t['task_id'] in selected_ids or not t['execution']['success']]}

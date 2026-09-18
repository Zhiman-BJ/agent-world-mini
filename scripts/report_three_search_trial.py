"""Summarize saved outputs and failures without changing any run artifacts."""
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / 'runs/three_search_recovered_20260917'


def main(run_roots=None, report_name='2026-09-17-three-search-results.md',
         title='三环境试跑产物与逐任务结果',
         intro='建图和 objective 复用旧 Step2；以下为网络恢复后的 Step3→5。'):
    lines = ['# ' + title, '', intro, '']
    roots = run_roots if run_roots is not None else [RUNS,
        ROOT / 'runs/three_search_runtime_retry_20260917',
        ROOT / 'runs/three_search_compose_retry_20260917']
    metas = sorted((p for root in roots for p in root.glob('*/*/run.json')),
                   key=lambda p: p.parent.name)
    latest = {}
    for meta in metas:
        bundle = meta.parent / 'intermediate/step_5_bundle.json'
        if bundle.exists():
            for task in json.loads(bundle.read_text())['tasks']:
                if 'compose_retry' in str(meta.parent) and not task.get('execution', {}).get('success'):
                    continue  # 转写跳过的旧执行失败不能覆盖更新的执行补跑结果。
                latest[(meta.parent.parent.name, task['task_id'])] = (meta.parent, task)
    accepted = [(key, run, task) for key, (run, task) in sorted(latest.items())
                if task.get('validation', {}).get('passed')]
    lines += ['## 最新通过任务文本', '',
              '按每个候选最近一次完成的 Step5 结果汇总；尚在进行的补跑未覆盖旧结果。历史结果和问题记录在后文。记录调用数包含程序保留的部分参数错误，不等同于人工核定的有效链长。', '']
    for (name, task_id), run, task in accepted:
        lines += [f'### {name} / {task_id}', '', task['task_text'], '',
                  f"记录调用数：{len(task['execution']['tool_calls'])}；[原始任务文件]({run / 'tasks.json'})。", '']
    lines += ['## 各轮完整记录', '']
    for meta in metas:
        run = meta.parent
        status = json.loads(meta.read_text())
        round_name = '运行故障补跑' if 'runtime_retry' in str(run) else '主轮'
        if 'compose_retry' in str(run):
            round_name = '仅补跑 Step4→5（复用原执行）'
        lines += [f'## {run.parent.name} — {round_name}', '', f'运行目录：`{run}`', '',
                  f"状态：{status['status']}；通过 {status.get('task_count', '待定')}；拒绝 {status.get('rejected_count', '待定')}。", '',
                  f"阶段耗时（秒）：`{json.dumps(status.get('stage_timings_seconds', {}))}`", '']
        events, tool_errors, usage = Counter(), Counter(), Counter()
        results = [json.loads(path.read_text()) for path in (run / 'tasks').glob('*/agent_result.json')]
        lines += [f"Step3 已返回 {len(results)} 条，其中执行成功 {sum(t['execution']['success'] for t in results)} 条。成功候选还会经 top-count/多样性筛选，未入选者仅保留 agent_result.json。", '']
        reused_execution = 'compose_retry' in str(run)
        if reused_execution:
            lines += ['此轮复用旧执行目录，旧搜索及工具调用不重复计入本轮事件数。', '']
        for path in (() if reused_execution else (run / 'tasks').glob('*/logs/*/stdout.log')):
            for line in path.read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    events['unparsed_event'] += 1
                    continue
                if event.get('type') in ('error', 'turn.failed'):
                    events[str(event.get('message', event.get('error', event)))[:300]] += 1
                if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'web_search':
                    events['completed_web_search'] += 1
        for path in (() if reused_execution else (run / 'tasks').glob('*/tool_calls.jsonl')):
            for line in path.read_text().splitlines():
                call = json.loads(line)
                if call.get('error'):
                    error = call['error']
                    if 'given schemas' in error:
                        error = 'output schema mismatch (full error in tool_calls.jsonl)'
                    tool_errors[call['tool'] + ': ' + error[:220]] += 1
        calls = run / 'llm_calls.jsonl'
        if calls.exists():
            for line in calls.read_text().splitlines():
                call = json.loads(line)
                for key, value in call.get('usage', {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] += value
        lines += ['### 调用、重连和工具错误', '',
                  '计数代表事件数，不代表失败任务数；同一会话可重连多次。token 为已返回的 usage，失败/超时可能缺失。', '']
        for label, counts in [('events', events), ('tool_errors', tool_errors), ('usage', usage)]:
            lines += [f'`{label}`', '```json', json.dumps(dict(counts), ensure_ascii=False, indent=2), '```', '']
        bundles = sorted((run / 'intermediate').glob('step_*_bundle.json'))
        if not bundles:
            continue
        for task in json.loads(bundles[-1].read_text()).get('tasks', []):
            execution = task.get('execution', {})
            validation = task.get('validation', {})
            lines += [f"### {task['task_id']}", '',
                      f"执行成功：{execution.get('success')}；调用数：{len(execution.get('tool_calls', []))}；最终通过：{validation.get('passed', '待定')}。", '',
                      task.get('task_text') or task.get('objective', ''), '']
            if execution.get('error'):
                lines += ['执行失败：' + str(execution['error']), '']
                if task.get('logic_reason'):
                    lines += ['执行者的判断（原文，仍需区分模型判断与实证）：', '', task['logic_reason'], '']
            for error in validation.get('errors', []):
                lines += ['- ' + str(error)]
            lines += ['']
    path = ROOT / 'reports' / report_name
    path.write_text('\n'.join(lines))
    print(path)


if __name__ == '__main__':
    main()

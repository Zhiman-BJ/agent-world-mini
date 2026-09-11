"""Review-only randomized business choices; no environment state mutations."""
from copy import deepcopy
import random

TOOL = {
    'name': 'review_select_plan',
    'description': '提交一个业务选择点的候选，由代码等概率选择；回退时只提交 choice_id 和 failure_evidence。',
    'inputSchema': {'type': 'object', 'additionalProperties': False, 'oneOf': [
        {'required': ['question', 'options']},
        {'required': ['choice_id', 'failure_evidence']},
    ], 'properties': {
        'question': {'type': 'string', 'minLength': 1},
        'options': {'type': 'array', 'minItems': 2, 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['description', 'basis'], 'properties': {
                'description': {'type': 'string', 'minLength': 1},
                'basis': {'type': 'string', 'minLength': 1},
            }}},
        'choice_id': {'type': 'integer', 'minimum': 1},
        'failure_evidence': {'type': 'string', 'minLength': 1},
    }},
}


class ReviewChoices:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.active = []
        self.next_id = 1

    def choose(self, arguments):
        if 'choice_id' in arguments:
            if set(arguments) != {'choice_id', 'failure_evidence'} or not arguments['failure_evidence'].strip():
                raise ValueError('回退须提供失败证据，不接受新候选')
            index = next((i for i, c in enumerate(self.active) if c['choice_id'] == arguments['choice_id']), None)
            if index is None:
                raise ValueError('选择点不存在或已失效')
            choice = self.active[index]
            if choice['selected_index'] is None:
                raise ValueError('该选择点已耗尽')
            choice['failed'].append({'index': choice['selected_index'], 'evidence': arguments['failure_evidence']})
            invalidated = [c['choice_id'] for c in self.active[index + 1:]]
            del self.active[index + 1:]
        else:
            if set(arguments) != {'question', 'options'} or not arguments['question'].strip():
                raise ValueError('新选择点须提供问题和候选')
            if any(c['selected_index'] is None for c in self.active):
                raise ValueError('存在耗尽选择点，须回退更早选择或拒绝任务')
            options = arguments['options']
            if any(not o['description'].strip() or not o['basis'].strip() for o in options):
                raise ValueError('候选及依据不能为空')
            if len({o['description'].strip() for o in options}) != len(options):
                raise ValueError('候选不能重复')
            choice = {'choice_id': self.next_id, 'question': arguments['question'],
                      'options': deepcopy(options), 'failed': [], 'selected_index': None}
            self.next_id += 1
            self.active.append(choice)
            invalidated = []
        remaining = [i for i in range(len(choice['options'])) if i not in {f['index'] for f in choice['failed']}]
        choice['selected_index'] = self.rng.choice(remaining) if remaining else None
        selected = choice['options'][choice['selected_index']] if remaining else None
        return deepcopy({'choice_id': choice['choice_id'], 'selected': selected,
                         'exhausted': not remaining, 'invalidated': invalidated, 'active_choices': self.active})

"""Structural checks for newly generated Direct necessity annotations."""
from ..codex_rubric_verifier.generation import validate_package as validate_rubric_package
from ..codex_tool_verifier.contracts import require


def validate_package(root, task_text=None):
    plan = validate_rubric_package(root, task_text)
    ids = set()

    def check(item, location):
        require(isinstance(item, dict), f'{location}: must be an object')
        rid = item.get('id')
        require(isinstance(rid, str) and bool(rid.strip()), f'{location}.id: must be a nonempty string')
        require(rid not in ids, f'{location}.id: duplicate rubric/subcriterion ID {rid}')
        ids.add(rid)
        for name in ('goal', 'necessity_reason', 'pass_condition', 'veto_condition'):
            value = item.get(name)
            require(isinstance(value, str) and bool(value.strip()),
                    f'{location}.{name}: must be a nonempty string')
        require(type(item.get('required_for_pass')) is bool,
                f'{location}.required_for_pass: must be a boolean')
        quotes = item.get('task_quotes')
        require(isinstance(quotes, list) and bool(quotes) and
                all(isinstance(q, str) and q.strip() for q in quotes),
                f'{location}.task_quotes: must be nonempty strings')
        if task_text is not None:
            require(all(q in task_text for q in quotes),
                    f'{location}.task_quotes: quote is not in task text')
        children = item.get('subcriteria')
        require(isinstance(children, list), f'{location}.subcriteria: must be an array (empty for a leaf)')
        for index, child in enumerate(children):
            check(child, f'{location}.subcriteria[{index}]')

    for index, rubric in enumerate(plan['rubrics']):
        check(rubric, f'rubrics[{index}]')
    return plan

from env_gen.tool_gen.semantic_contract import contract_issues, unconditional_inputs, guarded_required_inputs


def test_direct_inputs_are_required():
    assert unconditional_inputs("def run(arguments, context):\n x=arguments['x']\n return x\n") == {'x'}


def test_optional_default_is_supported():
    assert unconditional_inputs("def run(arguments, context):\n return arguments.get('x', 10)\n") == set()


def test_conditional_inputs_are_not_tightened():
    for code in [
        "if arguments.get('mode') == 'export':\n  return arguments['output_file']",
        "return arguments['x'] if arguments.get('x') else None",
        "return arguments.get('x') and arguments['y']",
        "return [arguments['x'] for i in context.rows]",
        "def helper():\n  return arguments['x']\n return 1",
    ]:
        assert unconditional_inputs('def run(arguments, context):\n ' + code.replace('\n', '\n ')) == set()


def test_required_check_is_limited_to_declared_fields():
    tool = {'internal': {'code': "def run(a,c):\n return a['x']"}, 'inputSchema': {'properties': {'x': {'type': 'integer'}}, 'required': []}}
    assert contract_issues(tool)[0]['rule'] == 'unconditional_input_optional'
    tool['inputSchema']['required'] = ['x']
    assert contract_issues(tool) == []


def test_empty_record_description_is_rejected_on_nonempty_input():
    field = {'type': 'string', 'minLength': 1, 'description': '处理腔体；无法由原件确定时为空。'}
    tool = {'inputSchema': {'properties': {'chamber': field}}}
    assert contract_issues(tool)[0]['rule'] == 'nonempty_input_described_empty'
    field['description'] = '处理腔体，不可为空。'
    assert contract_issues(tool) == []
    for text in ['不允许为空。', '不可以为空。', '不能为空。']:
        field['description'] = text
        assert contract_issues(tool) == []
    field['description'] = '可为空。'
    del field['minLength']
    assert contract_issues(tool) == []


def test_nested_input_schema_and_property_named_description():
    tool = {'inputSchema': {'properties': {'description': {'type': 'string', 'minLength': 1, 'description': '允许为空。'}}}}
    assert contract_issues(tool)[0]['pointer'] == '/inputSchema/properties/description'


def test_lookup_in_branch_condition_is_unconditional():
    assert unconditional_inputs("def run(args,c):\n if args['x']:\n  return 1\n return 0") == {'x'}


def test_explicit_missing_value_failure_is_a_requirement():
    assert guarded_required_inputs("def run(a,c):\n x=a.get('x')\n if not x: return {'success':False}\n return {'success':True}") == {'x'}
    assert guarded_required_inputs("def run(a,c):\n x=a.get('x')\n if not isinstance(x,str) or not x: return failure('x')\n return {'success':True}") == {'x'}


def test_optional_guard_and_successful_empty_response_are_supported():
    for code in [
        "x=a.get('x')\n if x is None: return {'success':True,'data':[]}",
        "x=a.get('x')\n if x is not None and not isinstance(x,str): return failure('x')",
        "x=a.get('x', 'default')\n if not x: return failure('x')",
        "x=a.get('x')\n if a.get('mode') == 'list': return {'success':True}\n if not x: return failure('x')",
        "x=a.get('x')\n if x is None: x=c.defaults\n if not x: return failure('x')",
        "x=a.get('x')\n if a.get('id'): x=c.record\n if not x: return failure('x')",
    ]:
        assert guarded_required_inputs('def run(a,c):\n ' + code.replace('\n', '\n ')) == set()


def test_single_enum_field_cannot_be_described_as_collection():
    tool = {'inputSchema': {'properties': {'group_by': {'type': 'string', 'enum': ['name'], 'description': '用于划分统计分组的一个或多个字段。'}}}}
    assert contract_issues(tool)[0]['rule'] == 'scalar_described_collection'


def test_assigning_an_input_dictionary_key_is_not_a_required_read():
    assert unconditional_inputs("def run(a,c):\n a['generated']=1\n return {'success':True}") == set()

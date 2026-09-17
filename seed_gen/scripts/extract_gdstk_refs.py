"""Static GDSTK export extraction from release C++ bindings and typed stubs."""
from __future__ import annotations

import ast
import re
from pathlib import Path

from seed_gen.scripts.extract_native_python_refs import _apply_native_doc
from seed_gen.scripts.extract_python_ref_tools import (
    _compact_text, _doc_sections, _function_record, _signature,
)


def extract_gdstk_stubs(root: Path) -> tuple[list[dict], list[str], dict]:
    """Use actual exports; fill stub omissions only from explicit native signatures."""
    stub = root / 'gdstk/_gdstk.pyi'
    documentation = root / 'python/docstrings.cpp'
    module_file = root / 'python/gdstk_module.cpp'
    tree = ast.parse(stub.read_text(encoding='utf-8-sig'), filename=str(stub))
    definitions = {n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    sources = {path: path.read_text(encoding='utf-8-sig') for path in sorted((root / 'python').glob('*.cpp'))}
    module_source = sources[module_file]
    docs = {}
    for match in re.finditer(r'PyDoc_STRVAR\(\s*(\w+)\s*,\s*R"([^\s()\\]*)\((.*?)\)\2"\s*\);', sources[documentation], re.S):
        docs[match[1]] = match[3]
    for match in re.finditer(r'PyDoc_STRVAR\(\s*(\w+)\s*,\s*("(?:\\.|[^"\\])*")\s*\);', sources[documentation]):
        docs[match[1]] = ast.literal_eval(match[2])
    if not docs:
        raise ValueError('GDSTK native docstrings not found')

    tables, table_files = {}, {}
    for path, text in sources.items():
        for table in re.finditer(r'static\s+(PyMethodDef|PyGetSetDef)\s+(\w+)\[\]\s*=\s*\{(.*?)\};', text, re.S):
            entries = {}
            active_table = re.sub(r'/\*.*?\*/|//[^\n]*', '', table[3], flags=re.S)
            for entry in re.finditer(r'\{\s*"([^"]+)"\s*,(.*?)\}', active_table, re.S):
                doc_names = re.findall(r'\b\w+_doc\b', entry[2])
                if len(doc_names) != 1 or doc_names[0] not in docs:
                    raise ValueError(f'GDSTK export lacks doc binding: {table[2]}.{entry[1]}')
                entries[entry[1]] = doc_names[0]
            tables[table[2]], table_files[table[2]] = entries, path
    active_module = re.sub(r'//[^\n]*', '', module_source)
    assignments = {(m[1], m[2]): m[3] for m in re.finditer(r'(\w+)\.(tp_methods|tp_getset)\s*=\s*(\w+)\s*;', active_module)}
    types = list(re.finditer(r'static\s+PyTypeObject\s+(\w+)\s*=\s*\{(.*?)\};', module_source, re.S))
    bindings, overloads, attributes, missing_stubs = {}, {}, {}, []
    used_files = {stub, documentation, module_file, root / 'gdstk/__init__.py'}
    records = []

    def prose(doc, name):
        first, separator, rest = doc.partition('\n\n')
        return rest if separator and first.lstrip().startswith(name+'(') else doc

    def function(name, doc_name, owner='', variants=()):
        doc = docs[doc_name]
        public = '.'.join(part for part in ('gdstk', owner, name) if part)
        source_name = owner if name == '__init__' else name
        if variants:
            node = max(variants, key=lambda n: len(n.args.posonlyargs)+len(n.args.args)+len(n.args.kwonlyargs))
            signature_source = 'release_typed_stub'
            if len(variants) > 1:
                overloads[public] = [_signature(variant) for variant in variants]
        else:
            signature = doc.split('\n\n', 1)[0].split(' -> ', 1)[0].strip()
            if not signature.startswith(source_name+'(') or not signature.endswith(')'):
                raise ValueError(f'GDSTK export missing stub and documented signature: {public}')
            parameters = signature[len(source_name)+1:-1]
            receiver = 'self, ' if owner and parameters else 'self' if owner else ''
            node = ast.parse(f'def {name}({receiver}{parameters}): ...').body[0]
            signature_source = 'native_documented_signature'
            missing_stubs.append(public)
        record = _function_record(node)
        _apply_native_doc(record, prose(doc, source_name))
        record['ori_description'] = _compact_text(doc)
        bindings[public] = {'doc_variable': doc_name, 'signature_source': signature_source}
        return record

    for match in types:
        native_type, body = match[1], match[2]
        public_match = re.search(r'"gdstk\.(\w+)"', body)
        if not public_match:
            raise ValueError(f'Unknown GDSTK native type: {native_type}')
        name = public_match[1]
        node = definitions.get(name)
        if not isinstance(node, ast.ClassDef):
            raise ValueError(f'GDSTK exported class has no typed definition: {name}')
        doc_names = re.findall(r'\b\w+_doc\b', body)
        if len(doc_names) != 1 or doc_names[0] not in docs:
            raise ValueError(f'GDSTK class lacks doc binding: {name}')
        type_doc = doc_names[0]
        grouped, typed_attributes = {}, {}
        for child in node.body:
            if isinstance(child, ast.FunctionDef):
                grouped.setdefault(child.name, []).append(child)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                typed_attributes[child.target.id] = ast.unparse(child.annotation)
        methods = [function('__init__', type_doc, name, grouped.get('__init__', []))]
        for slot in ('tp_methods', 'tp_getset'):
            table = assignments.get((native_type, slot))
            if table is None:
                continue
            if table not in tables:
                raise ValueError(f'GDSTK export table not found: {table}')
            used_files.add(table_files[table])
            for method_name, doc_name in tables[table].items():
                if method_name.startswith('_'):
                    continue
                if slot == 'tp_getset':
                    attributes[f'gdstk.{name}.{method_name}'] = {
                        'type': typed_attributes.get(method_name, ''), 'doc_variable': doc_name,
                        'description': _doc_sections(docs[doc_name])[0],
                    }
                else:
                    methods.append(function(method_name, doc_name, name, grouped.get(method_name, [])))
        records.append({'name': name, 'type': 'class', 'module': 'gdstk',
                        'input': ', '.join(ast.unparse(base) for base in node.bases),
                        'description': _doc_sections(prose(docs[type_doc], name))[0], 'function': methods})
    for name, doc_name in tables['gdstk_methods'].items():
        node = definitions.get(name)
        record = function(name, doc_name, variants=[node] if isinstance(node, ast.FunctionDef) else [])
        records.append({**record, 'type': 'function', 'module': 'gdstk'})
    if not records:
        raise ValueError('GDSTK has no public native exports')
    return records, [path.relative_to(root).as_posix() for path in sorted(used_files)], {
        'strategy': 'static_ast_and_release_gdstk_bindings',
        'namespace_policy': 'Public gdstk types and methods from release C++ export tables and __init__.py extension reexports. Inherited/private methods excluded; native data attributes kept as metadata, not counted as callable methods.',
        'docstring_adapter': 'Exact C++ PyDoc_STRVAR export binding; typed stub signatures, with explicit native doc signatures for stub omissions. Return annotation alone does not invent a Returns section.',
        'native_symbol_sources': bindings, 'native_overloads': overloads, 'native_data_attributes': attributes,
        'native_exports_missing_from_stubs': missing_stubs,
        'stub_limitations': 'Static release sources, no import or compilation; no inferred runtime-generated exports. Counts are reference operations, not implemented Agent actions.',
    }

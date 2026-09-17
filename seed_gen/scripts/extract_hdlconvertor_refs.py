"""Read Python-visible HDLConvertor Cython declarations without compiling C++."""
from __future__ import annotations
import ast
import inspect
from pathlib import Path
import re

from seed_gen.scripts.extract_python_ref_tools import _compact_text,_function_record


def extract_hdlconvertor(root: Path) -> tuple[list[dict],list[str],dict]:
    # Import locally: the general adapter imports this module at dispatch time.
    from seed_gen.scripts.extract_native_python_refs import _cython_docstrings
    files=['hdlConvertor/__init__.py','hdlConvertor/_hdlConvertor.pyx','hdlConvertor/verilogPreproc.pyx','hdlConvertor/python_ver_independent_str.pyx']
    exports={}
    for node in ast.parse((root/files[0]).read_text(encoding='utf-8')).body:
        if isinstance(node,ast.ImportFrom) and node.module=='_hdlConvertor' and node.level==1:
            exports.update({n.name:n.asname or n.name for n in node.names})
    if exports.get('HdlConvertorPy')!='HdlConvertor' or exports.get('ParseException')!='ParseException':
        raise ValueError('Unrecognized HDLConvertor official re-export table')
    main=(root/files[1]).read_text(encoding='utf-8')
    preproc=(root/files[2]).read_text(encoding='utf-8')
    if 'include "verilogPreproc.pyx"' not in main or 'include "python_ver_independent_str.pyx"' not in preproc:
        raise ValueError('Unrecognized HDLConvertor Cython include chain')
    records=[]
    origins={}
    constructors={}
    for filename in files[1:3]:
        path=root/filename
        lines=path.read_text(encoding='utf-8').splitlines()
        docs=_cython_docstrings(path)
        current=None
        owner=None
        methods={}
        for lineno,line in enumerate(lines,1):
            match=re.match(r'^(?:cdef\s+)?class\s+(\w+)(?:\((.*?)\))?\s*:',line)
            if match:
                owner,bases=match.groups()
                module='hdlConvertor' if owner in exports else 'hdlConvertor._hdlConvertor'
                name=exports.get(owner,owner)
                current=dict(name=name,type='class',module=module,input=bases or '',description=_compact_text(inspect.cleandoc(docs.get(('class',owner,''),''))),function=[])
                records.append(current)
                methods={}
                origins[f'{module}.{name}']={'path':filename,'line':lineno,'cython_class':owner}
                continue
            if line and not line[0].isspace() and not line.startswith('#'):
                current=None
                owner=None
                continue
            if current is None:
                continue
            method=re.match(r'^    def\s+(\w+)\s*\((.*)\)\s*:',line)
            if not method:
                continue
            name,args=method.groups()
            if name=='__cinit__':
                constructors[f'{current["module"]}.{current["name"]}']={'path':filename,'line':lineno,'declaration':line.strip(),'counted_as_method':False}
                continue
            if name.startswith('_') and name!='__init__':
                continue
            # Setter overloads do not replace the public getter/property record.
            previous=lines[lineno-2].strip() if lineno>1 else ''
            if previous.endswith('.setter') or previous.endswith('.deleter'):
                continue
            node=ast.parse(f'def {name}({args}):\n    pass\n').body[0]
            doc=inspect.cleandoc(docs.get(('method',owner,name),''))
            node.body.insert(0,ast.Expr(value=ast.Constant(value=doc)))
            record=_function_record(node)
            if name in methods:
                raise ValueError(f'Duplicate HDLConvertor public method {owner}.{name}')
            methods[name]=record
            current['function'].append(record)
            origins[f'{current["module"]}.{current["name"]}.{name}']={'path':filename,'line':lineno,'declaration':line.strip()}
    # The included file defines two Python helpers conditionally by Python major
    # version. Keep the real Python-3 declarations once, excluding C imports/data.
    text=(root/files[3]).read_text(encoding='utf-8')
    py3=text.split('if IS_PY3:',1)[1].split('\nelse:',1)[0]
    for name,args in re.findall(r'^    def\s+(\w+)\s*\((.*)\)\s*:',py3,re.M):
        if name.startswith('_'):
            continue
        node=ast.parse(f'def {name}({args}):\n    pass\n').body[0]
        records.append(dict(_function_record(node),type='function',module='hdlConvertor._hdlConvertor'))
        origins[f'hdlConvertor._hdlConvertor.{name}']={'path':files[3],'branch':'IS_PY3'}
    if not records or not any(r['name']=='HdlConvertor' and len(r['function'])==4 for r in records):
        raise ValueError('Incomplete HDLConvertor parser declaration extraction')
    return records,files,dict(strategy='static_cython_python_declarations',native_symbol_sources=origins,public_reexports=exports,cython_initializers=constructors,native_callable_count=sum(len(t.get('function',[])) if t['type']=='class' else 1 for t in records),namespace_policy='Re-exported HdlConvertorPy and ParseException use their actual public names. Included preprocessor classes live in the compiled hdlConvertor._hdlConvertor module. Internal cdef functions and C++ declarations are excluded.',constructor_policy='__cinit__ is Cython initialization metadata, not an invented Python __init__ tool. Source properties follow the existing reference-property counting convention.',limitations='Static source coverage for Python-visible declarations in the fixed include chain; C++ internals, inherited/dynamic methods, imported hdlConvertorAst symbols and Python special protocols are not duplicated. Not compared with a compiled runtime.')

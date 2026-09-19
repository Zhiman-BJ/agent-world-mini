"""Source-only Python-visible declarations from Kwant/tkwant Cython files."""
from __future__ import annotations

import ast
import copy
import inspect
from pathlib import Path
import re

from seed_gen.scripts.extract_python_ref_tools import _compact_text, _function_record


def split_arguments(text):
    parts, start, depth, quote = [], 0, 0, None
    for i, char in enumerate(text):
        if quote:
            if char==quote and (i==0 or text[i-1]!='\\'):
                quote=None
        elif char in "\"'":
            quote=char
        elif char in '([{':
            depth+=1
        elif char in ')]}':
            depth-=1
        elif char==',' and depth==0:
            parts.append(text[start:i].strip()); start=i+1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def python_signature(arguments):
    """Keep Python parameter names/defaults, remove explicit Cython type syntax."""
    python, types = [], {}
    for part in split_arguments(arguments):
        if part in ('*','/'):
            python.append(part); continue
        depth=0
        separator=None
        for i,char in enumerate(part):
            if char in '([{': depth+=1
            elif char in ')]}': depth-=1
            elif char=='=' and depth==0:
                separator=i; break
        left,sep,default=(part,'','') if separator is None else (part[:separator],'=',part[separator+1:])
        left=re.sub(r'\s+(?:not\s+)?None\s*$', '', left.strip())
        match=re.search(r'(\*{0,2}[A-Za-z_]\w*)$',left)
        if not match:
            raise ValueError('Unknown Cython parameter: '+part)
        name=match.group(1)
        type_name=left[:match.start()].strip()
        if type_name:
            types[name.lstrip('*')]=type_name
        python.append(name+(sep+default if sep else ''))
    return ', '.join(python), types


def read_declarations(path, module):
    lines=path.read_text(encoding='utf-8').expandtabs(4).splitlines()
    records, origins, initializers = [], {}, {}
    classes={}
    current=None
    def immediate_doc(start):
        while start<len(lines) and not lines[start].strip():
            start+=1
        if start==len(lines): return ''
        m=re.match(r"\s*[rRuU]*(\"\"\"|''')(.*)",lines[start])
        if not m: return ''
        quote, rest=m.groups()
        if quote in rest: return inspect.cleandoc(rest.split(quote,1)[0])
        body=[rest]
        for line in lines[start+1:]:
            if quote in line:
                body.append(line.split(quote,1)[0]); break
            body.append(line)
        return inspect.cleandoc('\n'.join(body))
    index=0
    while index<len(lines):
        line=lines[index]; stripped=line.strip(); indent=len(line)-len(line.lstrip())
        cls=re.match(r'^(?:cdef\s+)?class\s+(\w+)(?:\(([^)]*)\))?\s*:',line)
        if cls:
            name,bases=cls.groups()
            current={'name':name,'type':'class','module':module,'input':bases or '',
                     'description':_compact_text(immediate_doc(index+1)),'function':[]}
            classes[name]=current
            if not name.startswith('_'): records.append(current)
            origins[module+'.'+name]={'line':index+1,'declaration':stripped}
            index+=1; continue
        if indent==0 and stripped and not stripped.startswith(('#','@')):
            current=None
        expected=4 if current else 0
        prop=re.match(r'^\s*property\s+(\w+)\s*:',line) if indent==expected and current else None
        func=re.match(r'^\s*(?:def|cpdef)\s+(?:[\w.]+\s+)*?(\w+)\s*\(',line) if indent==expected else None
        if not (prop or func): index+=1; continue
        name=(prop or func).group(1)
        first=index
        declaration=line.strip()
        if func:
            balance=line.count('(')-line.count(')')
            while balance>0:
                index+=1
                if index>=len(lines): raise ValueError('Unterminated declaration')
                declaration+=' '+lines[index].strip()
                balance+=lines[index].count('(')-lines[index].count(')')
        doc=immediate_doc(index+1)
        index+=1
        owner=current['name'] if current else ''
        key=module+'.'+(owner+'.' if owner else '')+name
        if name=='__cinit__':
            initializers[key]={'line':first+1,'declaration':declaration}; continue
        if name.startswith('_') and name not in ('__init__','__call__'): continue
        if first and re.search(r'\.(setter|deleter)\s*$',lines[first-1]): continue
        if prop:
            arguments,types='self',{}
        else:
            args=declaration[declaration.index('(')+1:declaration.rfind(')')]
            arguments,types=python_signature(args)
        node=ast.parse(f'def {name}({arguments}):\n    pass\n').body[0]
        node.body.insert(0,ast.Expr(value=ast.Constant(value=doc)))
        record=_function_record(node)
        # Expose actual Python signature while retaining Cython declaration provenance.
        for arg,value in record['input'].items():
            if value is not None and not value.get('type') and arg in types:
                value['type']=types[arg]
        origins[key]={'line':first+1,'declaration':declaration,'cython_parameter_types':types}
        if current:
            if any(m['name']==name for m in current['function']): raise ValueError('Duplicate '+key)
            current['function'].append(record)
        else:
            records.append(dict(record,type='function',module=module))
    return records,origins,initializers,classes


def extract_transport(root, package, python_tools):
    records=copy.deepcopy(python_tools)
    paths=sorted((root/package).rglob('*.pyx'))
    files=[]; origins={}; constructors={}; classes={}
    for path in paths:
        relative=path.relative_to(root).as_posix()
        module=relative.removesuffix('.pyx').replace('/','.')
        tools,local,initializers,owned=read_declarations(path,module)
        records.extend(tools); files.append(relative)
        origins.update({k:dict(v,path=relative) for k,v in local.items()})
        constructors.update({k:dict(v,path=relative) for k,v in initializers.items()})
        classes[module]=owned
    bindings={}
    if package=='kwant':
        # Explicit source inheritance from a private Cython implementation class.
        operator=classes['kwant.operator']
        for name in ('Density','Current','Source'):
            record=operator[name]
            if record['input']!='_LocalOperator': raise ValueError('Kwant operator inheritance changed')
            for method in operator['_LocalOperator']['function']:
                if method['name'] in ('__call__','act','bind') and not any(m['name']==method['name'] for m in record['function']):
                    record['function'].append(copy.deepcopy(method))
                    bindings['kwant.operator.'+name+'.'+method['name']]='kwant.operator._LocalOperator.'+method['name']
        # Class-body binding to a real compiled Python function in the release.
        source=(root/'kwant/system.py').read_text(encoding='utf-8')
        if 'hamiltonian_submatrix = _system.hamiltonian_submatrix' not in source:
            raise ValueError('Kwant hamiltonian_submatrix binding changed')
        native=next(r for r in records if r['module']=='kwant._system' and r['name']=='hamiltonian_submatrix')
        owner=next(r for r in records if r['module']=='kwant.system' and r['name']=='System')
        owner['function'].append({k:copy.deepcopy(v) for k,v in native.items() if k not in ('type','module')})
        bindings['kwant.system.System.hamiltonian_submatrix']='kwant._system.hamiltonian_submatrix'
        files.append('kwant/system.py')
    records.sort(key=lambda r:(r['module'],r['name'],r['type']))
    return records,files,{'strategy':'static_ast_and_cython_python_declarations',
                          'cython_symbol_sources':origins,'cython_initializers':constructors,
                          'explicit_runtime_bindings':bindings,
                          'native_callable_count':sum(len(t.get('function',[])) if t['type']=='class' else 1 for t in records)-sum(len(t.get('function',[])) if t['type']=='class' else 1 for t in python_tools),
                          'limitations':'Python def/cpdef and public class/property declarations in release pyx files. C-only cdef helpers and unrelated C declarations excluded; __call__ retained for operator/solver protocols. Private implementation inheritance duplicated only for explicitly verified Kwant operator classes and System.hamiltonian_submatrix binding. Cython types retained as source metadata; no native build performed by extractor.'}

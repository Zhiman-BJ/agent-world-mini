"""Shared serialization for reviewed collection designs; no automatic selection."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from seed_gen.scripts.build_joint_scenario_seeds import file_sha, read, require
from seed_gen.scripts.select_python_ref_tools import _canonical_sha256

BASE=Path('seed_gen/scenario_collection')
RAW=Path('seed_gen/pypi_outputs/ori_all_funcs/scenario_collection')


def symbol(package,module,name,methods,capability,reason,construction_reason=None):
    value={'package':package,'module':module,'name':name,'type':'class' if methods is not None else 'function',
           'capability':capability,'reason':reason}
    if methods is not None: value['methods']=methods.split() if isinstance(methods,str) else methods
    if construction_reason: value['construction_reason']=construction_reason
    return value


def recipe(identifier,description,initial_state,steps,assertions,fixture,validation_status='passed_fixed_fixture'):
    return {'id':identifier,'description':description,'initial_state':initial_state,'fixture':fixture,
            'steps':[{'action':a,'tool_refs':refs} for a,refs in steps],'assertions':assertions,'validation_status':validation_status}


class Collection:
    def __init__(self, *, base=BASE, raw=RAW, inventory_base=BASE):
        self.base, self.raw, self.inventory_base = Path(base), Path(raw), Path(inventory_base)
        self.inventory=read(self.inventory_base/'inventory.json')
        self.specs={}
        for path in self.base.glob('*_sources.json'):
            for spec in read(path):
                require(spec['name'] not in self.specs,'Duplicate collection package spec')
                self.specs[spec['name']]=(spec,path)
        self.web={r['url']:r for path in (self.base/'research').glob('*web/index.json') for r in read(path)}

    def source(self,url,evidence,entities,tools,tasks):
        value=copy.deepcopy(self.web[url])
        require(value['status']==200 and value.get('title'),'Inspected readable source required')
        value['relevance']={'evidence':evidence,'entities':entities,'tools':tools,'tasks':tasks}
        return value

    def write(self,design):
        sid=design['scenario_id']
        row=next(r for r in self.inventory['scenarios'] if r['scenario_id']==sid)
        packages=[]; payloads={}
        for name,role,reason in design['packages']:
            spec,path=self.specs[name]
            raw_path=self.raw/f'{name}_{spec["tag"]}.json'
            payloads[name]=read(raw_path)
            packages.append({'name':name,'version':spec['tag'],'role':role,'reason':reason,
                             'raw_path':raw_path.as_posix(),'raw_sha256':_canonical_sha256(payloads[name]),
                             'release_manifest':path.as_posix()})
        symbols=copy.deepcopy(design['symbols'])
        for selected in symbols:
            matches=[t for t in payloads[selected['package']][0]['init_ref_tools'] if all(t[k]==selected[k] for k in ('module','name','type'))]
            require(len(matches)==1,f'Unknown/ambiguous source symbol: {selected}')
            raw=matches[0]
            if not raw.get('description') or any(not m.get('description') for m in raw.get('function',[]) if m['name'] in selected.get('methods',[])):
                selected['missing_description_reason']='必要来源接口说明为空，保留空值；场景用途见reason，不编造源文档。'
        (self.base/'research').mkdir(parents=True,exist_ok=True)
        (self.base/'profiles').mkdir(parents=True,exist_ok=True)
        research_path=self.base/f'research/{sid}.json'
        research_path.write_text(json.dumps(design,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        normalized=self.inventory_base/'classification.normalized.md'
        runtime=Path(design['runtime_report'])
        profile={'profile_version':'joint-scenario-1.0','scenario_id':sid,'index':row['index'],'checked_on':'2026-09-17',
                 'classification_source':normalized.as_posix(),'classification_sha256':file_sha(normalized),
                 'classification_upstream':{'path':self.inventory['classification_source'],'sha256':self.inventory['classification_sha256']},
                 'research_file':research_path.as_posix(),'research_sha256':file_sha(research_path),
                 'description':design['description'],'research_sources':design['sources'],'packages':packages,
                 'package_relation':'；'.join(f'{n}: {role}，{why}' for n,role,why in design['packages']),
                 'entities':design['entities'],'capabilities':[{'id':c,'required':True} for c in design['capabilities']],
                 'symbols':symbols,'target_all_func':{'min':50,'max':200},'bridges':design['bridges'],
                 'runtime_infrastructure':design['runtime_infrastructure'],'boundaries':design['boundaries'],
                 'tasks':design['tasks'],'runtime_reports':[{'path':runtime.as_posix(),'sha256':file_sha(runtime),'scope':design['runtime_scope']}]}
        if design.get('count_exception'): profile['count_exception']=design['count_exception']
        path=self.base/'profiles'/f'{sid}.json'
        path.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(path)

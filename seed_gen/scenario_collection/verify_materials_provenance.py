"""Real local AiiDA provenance on analytic energy payloads, without HPC."""
from __future__ import annotations

import json
import os
from pathlib import Path
import traceback

OUT = Path('seed_gen/scenario_collection/runtime/materials_provenance')
OUT.mkdir(parents=True,exist_ok=True)
# Set before importing AiiDA: do not create or change a personal profile.
os.environ['AIIDA_PATH'] = str((OUT/'config').resolve())

from aiida import load_profile, orm
from aiida.common.links import LinkType
from aiida.engine import calcfunction, workfunction
from aiida.manage import get_manager
from aiida.storage.sqlite_temp import SqliteTempBackend


@calcfunction
def energy_per_atom(total_energy, atom_count):
    """Normalize an explicitly supplied energy; this does not run DFT."""
    if atom_count.value <= 0:
        raise ValueError('atom_count must be positive')
    return orm.Float(total_energy.value/atom_count.value)


@calcfunction
def apply_correction(energy, correction):
    """Add an explicitly supplied correction in eV per atom."""
    return orm.Float(energy.value+correction.value)


@workfunction
def corrected_energy(total_energy, atom_count, correction):
    normalized=energy_per_atom(total_energy,atom_count)
    return apply_correction(normalized,correction)


def main():
    profile=SqliteTempBackend.create_profile('materials-provenance-fixture')
    load_profile(profile,allow_switch=True)
    try:
        total=orm.Float(-10).store()
        count=orm.Int(4).store()
        correction=orm.Float(.25).store()
        result,flow=corrected_energy.run_get_node(total,count,correction)
        assert flow.is_finished_ok and result.value == -2.25
        producer=result.base.links.get_incoming(link_type=LinkType.CREATE).one().node
        assert producer.process_label=='apply_correction' and producer.is_finished_ok
        inputs=producer.base.links.get_incoming(link_type=LinkType.INPUT_CALC).all_link_labels()
        assert set(inputs)=={'energy','correction'}
        parents=orm.QueryBuilder().append(orm.Float,filters={'id':result.pk},tag='result').append(
            orm.CalcFunctionNode,with_outgoing='result',project=['id','attributes.process_label']).all()
        assert parents==[[producer.pk,'apply_correction']]
        calls=flow.base.links.get_outgoing(link_type=LinkType.CALL_CALC).all()
        assert sorted(link.node.process_label for link in calls)==['apply_correction','energy_per_atom']

        wrong,wrong_flow=corrected_energy.run_get_node(total,count,orm.Float(250))
        assert wrong_flow.is_finished_ok and wrong.value==247.5 and wrong.value != -2.25
        repaired,new_flow=corrected_energy.run_get_node(total,count,orm.Float(.25))
        assert new_flow.is_finished_ok and repaired.value == -2.25
        assert len({flow.uuid,wrong_flow.uuid,new_flow.uuid})==3
        assert orm.load_node(wrong.uuid).value==247.5 and orm.load_node(result.uuid).value==-2.25
        try:
            corrected_energy.run_get_node(total,orm.Int(0),correction)
        except ValueError as exc:
            assert str(exc)=='atom_count must be positive'
        else:
            raise AssertionError('Invalid atom count did not fail')
        failed = orm.QueryBuilder().append(orm.CalcFunctionNode,
            filters={'attributes.process_state':'excepted'},project='*').all(flat=True)
        assert len(failed)==1 and failed[0].process_label=='energy_per_atom'
        invalid_count=failed[0].base.links.get_incoming(link_type=LinkType.INPUT_CALC,
            link_label_filter='atom_count').one().node
        assert invalid_count.value==0
        retry,retry_flow=corrected_energy.run_get_node(total,orm.Int(4),correction)
        assert retry_flow.is_finished_ok and retry.value==-2.25 and failed[0].is_excepted
        group=orm.Group(label='energy-correction-audit').store()
        group.add_nodes([flow,wrong_flow,new_flow,result,wrong,repaired])
        assert len(group.nodes)==6
        records={'result':result.uuid,'producer':producer.uuid,'normal_flow':flow.uuid,
                 'wrong_flow':wrong_flow.uuid,'repaired_flow':new_flow.uuid}
        report={'scenario_id':'01.03.03','status':'passed','versions':{'aiida-core':'2.9.2'},
                'tasks':[{'id':'trace_calculation_lineage','status':'passed','assertions':['result=-2.25 eV/atom','QueryBuilder identifies actual creator','two calcfunction children and correct input link labels']},
                         {'id':'repair_correction_units_with_provenance','status':'passed','assertions':['successful wrong process result247.5 rejected','correction250meV -> .25eV restores -2.25','three distinct immutable workflow histories','invalid atom count creates excepted child found by query','repair count and rerun succeeds without erasing failed node','group retains six nodes']}],
                'metrics':{'normal_eV_atom':result.value,'wrong_eV_atom':wrong.value,'repaired_eV_atom':repaired.value,
                           'calculation_children':len(calls),'group_nodes':len(group.nodes),'excepted_children':len(failed),'retry_eV_atom':retry.value},
                'runtime_ids':records,
                'scope':'Real AiiDA engine, SQLite temporary storage, provenance links and queries. Payload energies are supplied analytic values; no DFT, remote/HPC scheduler, daemon or checkpoint restart. Temp database is destroyed after checks; UUIDs are run evidence, not persistent external links.'}
        (OUT/'01.03.03.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report['metrics']),flush=True)
    finally:
        get_manager().reset_profile()


if __name__=='__main__':
    try:
        main()
    except Exception:
        (OUT/'attempt_failure.json').write_text(json.dumps({'status':'failed','traceback':traceback.format_exc()},indent=2),encoding='utf-8')
        raise

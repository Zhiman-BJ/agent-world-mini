"""Actual workflow graph, local scheduler, and synthetic VASP-log recovery fixtures."""
import importlib.metadata
import json
from pathlib import Path
import tempfile

from jobflow import Flow, JobStore, job, run_locally
from atomate2.vasp.flows.core import RelaxBandStructureMaker
from atomate2.vasp.powerups import update_user_incar_settings
from custodian.custodian import Custodian, Job as CustodianJob, CustodianError
from custodian.vasp.handlers import NonConvergingErrorHandler
from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp.inputs import Incar, Poscar

OUT=Path('seed_gen/scenario_collection/runtime/materials_workflow')


@job
def volume_fixture(a,scale=1.):
    """A manufactured job payload, not a DFT relaxation."""
    return (a*scale)**3


@job
def checksum_fixture(volume,multiplier=2.):
    return volume*multiplier


class ReplayedSCFJob(CustodianJob):
    """Emit fixed OSZICAR traces based on ALGO to exercise the real handler."""
    def __init__(self):
        self.attempts=0

    def run(self,directory='./'):
        self.attempts+=1
        directory=Path(directory)
        config=Incar.from_file(directory/'INCAR')
        steps=2 if config['ALGO'].lower()=='normal' else 3
        lines=[]
        for ionic in range(1,4):
            lines+=['       N       E                     dE             d eps       ncg     rms']
            lines += [f'DAV: {i:3d} -1.0000000 0.0000000 0.0000000 1 0.0000000' for i in range(1,steps+1)]
            lines += [f' {ionic:3d} F= -1.000000 E0= -1.000000 d E =0.000000']
        (directory/'OSZICAR').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def prepare_replay(directory):
    Incar({'ALGO':'Fast','NELM':3,'ISMEAR':1}).write_file(directory/'INCAR')
    structure=Structure(Lattice.cubic(5.43),['Si'],[[0,0,0]])
    Poscar(structure).write_file(directory/'POSCAR')
    Poscar(structure).write_file(directory/'CONTCAR')


def report(sid,tasks,metrics,scope):
    payload={'scenario_id':sid,'status':'passed','versions':{p:importlib.metadata.version(p) for p in ('atomate2','jobflow','custodian','pymatgen-core')},
             'tasks':[{'id':t,'status':'passed'} for t in tasks],'metrics':metrics,'scope':scope}
    (OUT/f'{sid}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(sid,json.dumps(metrics))


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    structure=Structure.from_spacegroup(227,Lattice.cubic(5.431),['Si'],[[0,0,0]])
    initial=RelaxBandStructureMaker().make(structure)
    bad=update_user_incar_settings(initial,{'ENCUT':200})
    assert any(j.maker.input_set_generator.user_incar_settings['ENCUT']!=520 for j,_ in bad.iterflow())
    fixed=update_user_incar_settings(bad,{'ENCUT':520})
    nodes=list(fixed.iterflow())
    assert [j.name for j,_ in nodes]==['relax 1','relax 2','static','non-scf uniform','non-scf line']
    seen=set()
    for j,_ in nodes:
        assert set(j.input_uuids)<=seen
        assert j.maker.input_set_generator.user_incar_settings['ENCUT']==520
        seen.add(j.uuid)
    assert len(seen)==5
    # Execute a dependency graph with fixed analytic payloads, no external solver.
    def execute(multiplier):
        parent=volume_fixture(2.,scale=.5)
        child=checksum_fixture(parent.output,multiplier=multiplier)
        flow=Flow([child,parent],output=child.output)
        store=JobStore.from_dict_spec({'docs_store':{'type':'MemoryStore'}})
        # Execution resolves OutputReferences in place, so inspect the input
        # dependency before running and the persisted result after running.
        assert set(child.input_uuids)=={parent.uuid}
        run_locally(flow,store=store,log=False,ensure_success=True)
        value=store.get_output(child.uuid)
        assert store.count()==2
        return value
    wrong=execute(3.)
    assert wrong!=2.
    right=execute(2.)
    assert right==2.
    report('01.03.01',['repair_materials_flow_settings','execute_dependency_fixture'],
           {'job_names':[j.name for j,_ in nodes],'encut_eV':520,'local_wrong_output':wrong,'local_repaired_output':right},
           'Real atomate2 VASP workflow construction/configuration and jobflow local execution with analytic payloads. No VASP binary, PAW data, DFT energy, band or DOS computation.')
    handler=NonConvergingErrorHandler(nionic_steps=2)
    with tempfile.TemporaryDirectory(prefix='custodian-seed-',dir=OUT) as temp:
        directory=Path(temp)
        prepare_replay(directory)
        job0=ReplayedSCFJob(); job0.run(directory)
        assert handler.check(directory)
        job1=ReplayedSCFJob()
        logs=Custodian([handler],[job1],max_errors=3,directory=str(directory)).run()
        assert job1.attempts==2
        assert Incar.from_file(directory/'INCAR')['ALGO']=='Normal'
        assert not handler.check(directory)
        assert logs[0]['corrections'][0]['errors']==['Non-converging job']
        (OUT/'custodian_recovery.json').write_text(json.dumps(logs,indent=2,default=str)+'\n',encoding='utf-8')
        # The same injected failure must not pass with an exhausted retry budget.
        prepare_replay(directory)
        limited=ReplayedSCFJob()
        rejected=False
        try:
            Custodian([NonConvergingErrorHandler(nionic_steps=2)],[limited],max_errors=1,directory=str(directory)).run()
        except CustodianError:
            rejected=True
        assert rejected and limited.attempts==1
        prepare_replay(directory)
        recovered=ReplayedSCFJob()
        Custodian([NonConvergingErrorHandler(nionic_steps=2)],[recovered],max_errors=3,directory=str(directory)).run()
        assert recovered.attempts==2 and not handler.check(directory)
    report('01.03.02',['repair_scf_log_configuration','repair_retry_budget'],
           {'attempts_after_correction':2,'final_ALGO':'Normal','exhausted_budget_rejected':rejected,'diagnostic':'three ionic steps, NELM3 reached before correction, 2 electronic steps after'},
           'Real custodian NonConvergingErrorHandler, VaspModder, INCAR/OSZICAR parsing and retry loop; external SCF process is a deterministic synthetic trace replayer. Does not prove VASP physical convergence or walltime scheduling.')


if __name__=='__main__': main()

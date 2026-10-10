"""Offline commercial-command contract; never opens a Cadence connection."""
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from skillbridge import Workspace, ParseError

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/commercial_bridge'
OPEN_LAYOUT='dbOpenCellViewByType("LIB" "DIVIDER" "layout" "maskLayout" "a" )'
PATH_VERTICAL='rodCreatePath( ?cvId __py_remote_cv ?layer (list "Metal2" "drawing") ?width 0.08 ?pts (list (list 0.0 0.0) (list 0.0 5.0)))'
PATH_HORIZONTAL='rodCreatePath( ?cvId __py_remote_cv ?layer (list "Metal2" "drawing") ?width 0.08 ?pts (list (list 0.0 0.0) (list 5.0 0.0)))'

class FixtureChannel:
    """Strict recorded text expectations, not an EDA simulator or SKILL engine."""
    def __init__(self, expectations):
        self.remaining=list(expectations);self.requests=[];self.closed=False;self.flushed=False
        self.max_transmission_length=1_000_000
    def send(self, command):
        expected,response=self.remaining.pop(0)
        assert str(command)==expected,(str(command),expected)
        self.requests.append(str(command))
        return response
    def flush(self):self.flushed=True
    def close(self):self.closed=True

def paths():
    channel=FixtureChannel([(OPEN_LAYOUT,'Remote("__py_remote_cv")'),
      (PATH_VERTICAL,'Remote("__py_remote_path0")'),(PATH_HORIZONTAL,'Remote("__py_remote_path1")'),
      ('dbSave(__py_remote_cv )','True')])
    ws=Workspace(channel=channel,id_='fixed_layout_contract')
    cv=ws.db.open_cell_view_by_type('LIB','DIVIDER','layout','maskLayout','a')
    wrong=str(ws.rod.create_path.lazy(cv_id=cv,layer=['Metal2','drawing'],width=.8,pts=[[0.,0.],[0.,5.]]))
    assert wrong!=PATH_VERTICAL and '?width 0.8 ' in wrong
    fixed=str(ws.rod.create_path.lazy(cv_id=cv,layer=['Metal2','drawing'],width=.08,pts=[[0.,0.],[0.,5.]]))
    assert fixed==PATH_VERTICAL
    for points in [[[0.,0.],[0.,5.]],[[0.,0.],[5.,0.]]]:
        ws.rod.create_path(cv_id=cv,layer=['Metal2','drawing'],width=.08,pts=points)
    assert ws.db.save(cv) is True
    ws.flush();ws.close()
    assert channel.closed and channel.flushed and not channel.remaining
    return dict(commands=channel.requests,wrong_width_command=wrong,repaired_command=fixed,
      domain_execution='not_executed',fixture_response_only=True)

def check_save():
    expected=[
      ('dbOpenCellViewByType("LIB" "DIVIDER" "schematic" "" "r" )','Remote("__py_remote_readonly")'),
      ('dbCheck(__py_remote_readonly )',"error('read-only cell view rejected by fixed protocol fixture')"),
      ('dbOpenCellViewByType("LIB" "DIVIDER" "schematic" "" "a" )','Remote("__py_remote_editable")'),
      ('dbCheck(__py_remote_editable )','True'),('dbSave(__py_remote_editable )','True')]
    channel=FixtureChannel(expected);ws=Workspace(channel=channel,id_='fixed_schematic_contract')
    wrong=ws.db.open_cell_view_by_type('LIB','DIVIDER','schematic','','r')
    error=None
    try:ws.db.check(wrong)
    except ParseError as exc:error=str(exc)
    assert error and 'read-only cell view' in error
    assert len(channel.requests)==2 and not any('dbSave' in c for c in channel.requests)
    fixed=ws.db.open_cell_view_by_type('LIB','DIVIDER','schematic','','a')
    assert ws.db.check(fixed) is True
    assert ws.db.save(fixed) is True
    ws.close();assert channel.closed and not channel.remaining
    return dict(commands=channel.requests,error=error,no_save_after_error=True,
      domain_execution='not_executed',fixture_response_only=True)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    repeats=[{'paths':paths(),'save':check_save()} for _ in range(2)]
    assert repeats[0]==repeats[1]
    result=dict(scenario_id='03.11.04',status='passed',versions={'skillbridge':version('skillbridge')},
      tasks=[dict(id='repair_layout_command_width_contract',status='passed',**repeats[0]['paths']),
        dict(id='repair_schematic_view_and_save_sequence',status='passed',**repeats[0]['save'])],
      repeats=2,repeat_equal=True,backend='strict_offline_text_fixture_no_cadence',
      oracle='Frozen independent SKILL command strings from the official example structure; actual released translator and RemoteFunction produce commands, actual decoder maps scripted server error. Fixture responses never prove remote design changes.',
      boundaries=['No Virtuoso/SKILL server/license/PDK is used or emulated. No schematic/layout object is actually created or saved in Cadence.','Tests verify serialization, argument names/values, remote-reference binding, error mapping and command order only; DRC/LVS/simulation are unvalidated.','Recorded response True is a transport fixture, never a physical or business-success oracle.'],
      executed_script=Path(__file__).as_posix(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (OUT/'03.11.04.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print([(t['id'],t['status']) for t in result['tasks']]);print('Two independent offline repetitions matched; Cadence not executed.')

if __name__=='__main__':main()

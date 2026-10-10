"""Local protocol, control, scheduling and maintenance fixed fixtures.

No external endpoint or real equipment. Protocol codecs and OPC UA loopback
are real APIs; canonical state/recipe semantics and factory routes are explicit
engineering fixture data, not capabilities purportedly built into those APIs.
"""
import asyncio
import importlib.metadata
import json
import math
from pathlib import Path
import socket
import struct
import warnings
warnings.filterwarnings('ignore',category=UserWarning,module='do_mpc')
import numpy as np
import pandas as pd
import control as ct
import do_mpc
from asyncua import Server,Client,ua
from secsgem import hsms,gem,common
from secsgem.secs import functions as sf
from ortools.sat.python import cp_model
import simpy
import ruptures as rpt
from river.drift import PageHinkley
from reliability.Distributions import Weibull_Distribution

BASE=Path('seed_gen/scenario_collection/l1_test_fab');OUT=BASE/'runtime/control'
def task(identifier,**kw):return dict(id=identifier,status='passed',**kw)
def roundtrip(cls,value):
    msg=cls(value);encoded=msg.encode();decoded=cls();decoded.decode(encoded)
    return decoded.get(),encoded

def protocol():
    payload=sf.SecsS01F01().encode();header=hsms.HsmsStreamFunctionHeader(123,1,1,True,7)
    raw=hsms.HsmsMessage(header,payload).blocks[0].encode()
    # Independent network-byte-order header oracle; W bit is stream high bit.
    length=struct.unpack('>I',raw[:4])[0]
    assert length==len(raw)-4 and raw[4:14]==struct.pack('>HBBBBI',7,129,1,0,0,123)
    a,b=socket.socketpair()
    try:
        a.sendall(raw);received=b.recv(4096)
    finally:a.close();b.close()
    msg=hsms.HsmsMessage.from_block(hsms.HsmsBlock.decode(received))
    assert msg.header.session_id==7 and msg.header.system==123 and msg.header.require_response
    wrong=hsms.HsmsMessage(hsms.HsmsStreamFunctionHeader(124,1,2,False,7),b'').blocks[0].encode()
    wrong_system=hsms.HsmsMessage.from_block(hsms.HsmsBlock.decode(wrong)).header.system
    assert wrong_system!=msg.header.system
    correct=hsms.HsmsMessage(hsms.HsmsStreamFunctionHeader(123,1,2,False,7),b'').blocks[0].encode()
    assert hsms.HsmsMessage.from_block(hsms.HsmsBlock.decode(correct)).header.system==123
    truncated=raw[:-1]
    assert struct.unpack('>I',truncated[:4])[0]!=len(truncated)-4
    return [task('repair_hsms_correlation',wrong_response_system=wrong_system,repaired_system=123,session_id=7,header_hex=raw[4:14].hex(),transport='local socketpair bytes'),
            task('reject_incomplete_hsms',bad_declared_length=length,bad_actual_length=len(truncated)-4,repaired_length=len(raw)-4,oracle='4-byte network length plus 10-byte header')]

def events_alarms():
    source={'DATAID':1,'CEID':50,'RPT':[{'RPTID':100,'V':['W01',123]}]};decoded,_=roundtrip(sf.SecsS06F11,source)
    wrong_mapping={100:['pressure','wafer']};right_mapping={100:['wafer','pressure']}
    values=decoded['RPT'][0]['V'];wrong=dict(zip(wrong_mapping[100],values));correct=dict(zip(right_mapping[100],values))
    assert wrong['pressure']=='W01' and correct=={'wafer':'W01','pressure':123}
    # Disabled transmission tests actual package alarm state lifecycle offline.
    equip=gem.GemEquipmentHandler(hsms.HsmsSettings(device_type=common.DeviceType.EQUIPMENT,connect_mode=hsms.HsmsConnectMode.PASSIVE,port=59998))
    equip.alarms[25]=gem.Alarm(25,'pressure','pressure high',1,10025,20025)
    rejected=False
    try:equip.set_alarm(99)
    except ValueError:rejected=True
    assert rejected and not equip.alarms[25].enabled
    equip.set_alarm(25);assert equip.alarms[25].set
    equip.set_alarm(25);assert equip.alarms[25].set
    equip.clear_alarm(25);assert not equip.alarms[25].set
    alarm,_=roundtrip(sf.SecsS05F01,{'ALCD':129,'ALID':25,'ALTX':'pressure high'})
    assert alarm['ALID']==25 and alarm['ALCD']&128
    return [task('repair_report_variable_order',wrong=wrong,repaired=correct,event_id=decoded['CEID']),
            task('repair_alarm_identity',unknown_alarm_rejected=True,state_sequence=[False,True,True,False],decoded_alarm=alarm,boundary='Alarm transmission disabled; no GEM host negotiation performed')]

def remote_recipe():
    def request(recipe):return roundtrip(sf.SecsS02F41,{'RCMD':'START','PARAMS':[{'CPNAME':'PPID','CPVAL':recipe}]})[0]
    recipes={'ETCH_A':{'power_w':100,'time_s':10}}
    def execute(msg,online=True):
        # Explicit simulated equipment semantics, not secsgem behavior.
        params={x['CPNAME']:x['CPVAL'] for x in msg['PARAMS']};ppid=params.get('PPID')
        accepted=online and msg['RCMD']=='START' and ppid in recipes
        reply,_=roundtrip(sf.SecsS02F42,{'HCACK':0 if accepted else 1,'PARAMS':[]})
        return reply,{'state':'running' if accepted else 'idle','ppid':ppid if accepted else None}
    bad,bstate=execute(request('MISSING'));good,state=execute(request('ETCH_A'))
    assert bad['HCACK']==1 and bstate['state']=='idle' and good['HCACK']==0 and state['ppid']=='ETCH_A'
    off,offstate=execute(request('ETCH_A'),online=False);assert off['HCACK']==1 and offstate['state']=='idle'
    return [task('repair_recipe_reference',bad_reply=bad,good_reply=good,result=state,recipe=recipes['ETCH_A']),
            task('repair_remote_precondition',offline_rejected=off,online_accepted=good,boundary='Codec validates message; local engineering state machine validates recipe/online policy')]

async def opcua_run():
    reserve=socket.socket();reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1];reserve.close()
    endpoint=f'opc.tcp://127.0.0.1:{port}/fab-fixture/'
    server=Server();await server.init();server.set_endpoint(endpoint);server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    ns=await server.register_namespace('urn:fab:fixed-fixture');equipment=await server.nodes.objects.add_object(ns,'ETCH01')
    pressure=await equipment.add_variable(ns,'Pressure_kPa',1.0);await pressure.set_writable()
    async with server:
        async with Client(endpoint) as client:
            node=client.get_node(pressure.nodeid);initial=await node.read_value();assert initial==1.
            wrong_type=False
            try:await node.write_value('bad-unit')
            except ua.UaStatusCodeError:wrong_type=True
            assert wrong_type and await node.read_value()==1.
            await node.write_value(2.5);assert await pressure.read_value()==2.5
            wrong_node=client.get_node(ua.NodeId('missing',ns));unknown=False
            try:await wrong_node.read_value()
            except ua.UaStatusCodeError:unknown=True
            assert unknown
            browsed=await client.nodes.objects.get_child([f'{ns}:ETCH01',f'{ns}:Pressure_kPa'])
            assert await browsed.read_value()==2.5
    return [task('repair_opcua_value_type',initial=1.,wrong_type_rejected=True,repaired_value=2.5,transport='real localhost OPC UA'),
            task('repair_opcua_node_identity',missing_node_rejected=True,browse_path=['ETCH01','Pressure_kPa'],repaired_value=2.5,boundary='NoSecurity only bound to localhost fixture; no production auth validation')]
def opcua():return asyncio.run(opcua_run())

def canonical_events():
    messages=[]
    for equip,wafer,timev in [('E1','W1',1),('E2','W1',2),('E1','W2',3)]:
        msg,_=roundtrip(sf.SecsS06F11,{'DATAID':timev,'CEID':50,'RPT':[{'RPTID':100,'V':[equip,wafer,timev]}]});values=msg['RPT'][0]['V'];messages.append(dict(equipment=values[0],wafer=values[1],time=values[2],lot='L01'))
    frame=pd.DataFrame(messages);assert len(frame.drop_duplicates('wafer'))==2
    assert not frame.duplicated(['lot','wafer','equipment','time']).any()
    ordered=frame.sort_values('time');assert ordered.equipment.tolist()==['E1','E2','E1']
    recipe=pd.DataFrame({'equipment':['E1','E2'],'recipe':['ETCH_A','ETCH_B']})
    linked=frame.merge(recipe,on='equipment',validate='many_to_one');assert linked.recipe.tolist()==['ETCH_A','ETCH_B','ETCH_A']
    rejected=False
    try:frame.merge(pd.concat([recipe,recipe.iloc[[0]]]),on='equipment',validate='many_to_one')
    except pd.errors.MergeError:rejected=True
    assert rejected
    return [task('repair_canonical_event_key',wrong_unique=2,repaired_unique=3,canonical=ordered.to_dict(orient='records')),
            task('repair_equipment_mapping',duplicate_mapping_rejected=True,joined=linked.to_dict(orient='records'),oracle_rows=3)]

def dynamics():
    t=np.linspace(0,10,101);model=ct.tf([1],[2,1]);response=ct.step_response(model,t)
    expected=1-np.exp(-t/2);assert np.allclose(response.outputs,expected,rtol=1e-10,atol=1e-10)
    wrong=ct.step_response(ct.tf([1],[.002,1]),t).outputs;assert abs(wrong[10]-expected[10])>.5
    sampled=ct.sample_system(model,1);A,B,C,D=ct.ssdata(sampled)
    assert math.isclose(A.item(),math.exp(-.5),rel_tol=1e-12)
    tt=np.arange(6);discrete=ct.forced_response(sampled,tt,np.ones(6)).outputs
    oracle=[1-math.exp(-k/2) for k in range(6)];assert np.allclose(discrete,oracle,atol=1e-10)
    return [task('repair_time_constant_units',wrong_at1=float(wrong[10]),correct_at1=float(response.outputs[10]),oracle_at1=1-math.exp(-.5)),
            task('verify_discrete_model',sample_time_s=1.,pole=float(A.item()),response=discrete.tolist(),oracle=oracle)]

def feedback():
    plant=ct.tf([1],[1,1]);negative=ct.feedback(2*plant,1,sign=-1);positive=ct.feedback(2*plant,1,sign=1)
    assert ct.poles(negative).real.tolist()==[-3.] and ct.poles(positive).real.tolist()==[1.]
    t=np.linspace(0,3,31);y=ct.step_response(negative,t).outputs
    assert np.allclose(y,(2/3)*(1-np.exp(-3*t)),atol=1e-10)
    # Run-to-run engineering law u[k+1]=u[k]+g*(target-y[k]), static y=u.
    stable=ct.ss([[.5]],[[.5]],[[1]],[[0]],dt=1);bad=ct.ss([[-1.5]],[[2.5]],[[1]],[[0]],dt=1)
    stable_y=ct.forced_response(stable,np.arange(7),np.ones(7)).outputs;bad_y=ct.forced_response(bad,np.arange(7),np.ones(7)).outputs
    assert np.allclose(stable_y,[1-.5**k for k in range(7)]) and abs(bad_y[-1]-1)>1
    return [task('repair_feedback_sign',wrong_pole=1.,repaired_pole=-3.,oracle_final=float((2/3)*(1-math.exp(-9)))),
            task('repair_run_to_run_gain',wrong_gain=2.5,correct_gain=.5,wrong_response=bad_y.tolist(),correct_response=stable_y.tolist(),oracle_end=1-.5**6)]

def mpc_controller(limit):
    model=do_mpc.model.Model('discrete');x=model.set_variable('_x','x');u=model.set_variable('_u','u');model.set_rhs('x',.8*x+u);model.setup()
    mpc=do_mpc.controller.MPC(model);mpc.settings.n_horizon=1;mpc.settings.t_step=1;mpc.settings.supress_ipopt_output()
    mpc.set_objective(mterm=(x-1)**2,lterm=(x-1)**2);mpc.set_rterm(u=.01)
    mpc.bounds['lower','_u','u']=-limit;mpc.bounds['upper','_u','u']=limit;mpc.setup();mpc.x0=np.array([0.]);mpc.set_initial_guess()
    simulator=do_mpc.simulator.Simulator(model);simulator.set_param(t_step=1);simulator.setup();simulator.x0=np.array([0.])
    return mpc,simulator

def mpc_tasks():
    bad,_=mpc_controller(.05);wrong=float(bad.make_step(np.array([0.])).item())
    good,sim=mpc_controller(.5);first=float(good.make_step(np.array([0.])).item());assert abs(wrong-.05)<1e-6 and abs(first-.5)<1e-6
    state=sim.make_step(np.array([[first]]));assert abs(state.item()-.5)<1e-6
    states=[float(state.item())];controls=[first]
    for _ in range(7):
        u=good.make_step(state);before=state.item();state=sim.make_step(u);assert abs(state.item()-(.8*before+u.item()))<1e-8;states.append(float(state.item()));controls.append(float(u.item()))
    assert abs(states[-1]-1)<.01 and max(abs(v) for v in controls)<=.500001
    fresh,freshsim=mpc_controller(.5);reset=float(fresh.make_step(np.array([0.])).item());assert abs(reset-first)<1e-9 and freshsim.x0['x'].full().item()==0
    return [task('repair_mpc_actuator_bound',wrong_u0=wrong,correct_u0=first,analytic_unconstrained_u0=1/1.01,analytic_constrained_u0=.5),
            task('replay_and_reset_mpc',states=states,controls=controls,reset_u0=reset,oracle='Each x_next=.8*x+u; fresh controller/simulator starts at zero')]

def mhe_run(value,arrival_weight=1e-8):
    model=do_mpc.model.Model('discrete');x=model.set_variable('_x','x');model.set_rhs('x',x);model.set_meas('y',x);model.setup()
    estimator=do_mpc.estimator.MHE(model);estimator.settings.n_horizon=3;estimator.settings.t_step=1;estimator.settings.meas_from_data=True;estimator.settings.supress_ipopt_output()
    estimator.set_default_objective(P_x=np.array([[arrival_weight]]),P_v=np.array([[1.]]));estimator.setup();estimator.x0=np.array([4.]);estimator.set_initial_guess()
    return [float(estimator.make_step(np.array([value])).item()) for _ in range(4)]

def mhe_tasks():
    wrong=mhe_run(2000.);correct=mhe_run(2.);assert abs(correct[-1]-2)<1e-5 and wrong[-1]>1999
    pinned=mhe_run(2.,1000);assert pinned[0]>3.9 and abs(correct[0]-2)<1e-5
    return [task('repair_mhe_measurement_units',wrong_last=wrong[-1],repaired_estimates=correct,oracle_constant_state=2.),
            task('repair_mhe_arrival_weight',wrong_first=pinned[0],repaired_first=correct[0],oracle='With negligible arrival weight and noiseless constant measurement, least squares state=2')]

def component_flow(capacity=1,buffer_capacity=1):
    env=simpy.Environment();machine=simpy.Resource(env,capacity);buffer=simpy.Store(env,buffer_capacity);rows=[]
    def source():
        for i in range(3):yield buffer.put('L'+str(i))
    def worker():
        for _ in range(3):
            lot=yield buffer.get();env.process(process(lot))
    def process(lot):
        with machine.request() as req:
            yield req;start=env.now;yield env.timeout(2);rows.append(dict(lot=lot,start=start,end=env.now))
    env.process(source());env.process(worker());env.run();return rows

def factory_components():
    bad=component_flow(2);good=component_flow();assert [x['end'] for x in bad]==[2,2,4] and [x['end'] for x in good]==[2,4,6]
    zero=False
    try:component_flow(buffer_capacity=0)
    except ValueError:zero=True
    assert zero
    return [task('repair_machine_capacity',wrong=bad,repaired=good,oracle_completion=[2,4,6]),
            task('repair_buffer_capacity',zero_buffer_rejected=True,repaired_count=3,oracle_conservation={'released':3,'completed':3,'lost':0},boundary='SimPy source/buffer/processor composition authored in fixture; FactorySimPy has no stable release')]

def optimize(calendar=True):
    model=cp_model.CpModel();starts=[model.new_int_var(0,10,'s'+str(i)) for i in range(2)];ends=[model.new_int_var(0,10,'e'+str(i)) for i in range(2)]
    intervals=[model.new_interval_var(starts[i],d,ends[i],'i'+str(i)) for i,d in enumerate([3,2])]
    if calendar:intervals.append(model.new_fixed_size_interval_var(3,2,'PM'))
    model.add_no_overlap(intervals);model.add(starts[1]>=ends[0]);model.minimize(ends[1]);solver=cp_model.CpSolver();solver.parameters.num_workers=1;solver.parameters.random_seed=9
    result=solver.solve(model);assert result==cp_model.OPTIMAL
    return dict(start=[solver.value(v) for v in starts],end=[solver.value(v) for v in ends],makespan=int(solver.objective_value))

def general_schedule():
    bad=optimize(False);good=optimize(True);assert bad['makespan']==5 and good==dict(start=[0,5],end=[3,7],makespan=7)
    # Independent finite enumeration checks the known tiny optimization optimum.
    feasible=[(a,b) for a in range(8) for b in range(9) if b>=a+3 and (a+3<=3 or a>=5) and (b+2<=3 or b>=5)]
    oracle=min(b+2 for a,b in feasible);assert oracle==7
    model=cp_model.CpModel();x=model.new_int_var(0,1,'x');model.add(x>=2);solver=cp_model.CpSolver();status=solver.solve(model);assert status==cp_model.INFEASIBLE
    repaired=cp_model.CpModel();x2=repaired.new_int_var(0,2,'x');repaired.add(x2>=2);assert solver.solve(repaired)==cp_model.OPTIMAL and solver.value(x2)==2
    return [task('repair_general_schedule_calendar',wrong=bad,repaired=good,independent_enumerated_makespan=oracle),
            task('repair_infeasible_domain',bad_status='INFEASIBLE',repaired_status='OPTIMAL',oracle_value=2)]

def amhs_sim(capacity=1,travel=2):
    env=simpy.Environment();vehicle=simpy.Resource(env,capacity);machine=simpy.Resource(env,1);rows=[]
    def lot(i):
        with vehicle.request() as req:yield req;depart=env.now;yield env.timeout(travel);arrival=env.now
        with machine.request() as req:yield req;yield env.timeout(2);rows.append(dict(lot=i,depart=depart,arrival=arrival,end=env.now))
    for i in ['A','B']:env.process(lot(i))
    env.run();return rows
def amhs():
    bad=amhs_sim(2);good=amhs_sim();assert [r['arrival'] for r in bad]==[2,2] and [r['arrival'] for r in good]==[2,4]
    wrong_unit=amhs_sim(travel=120);assert wrong_unit[0]['arrival']==120
    assert good==[dict(lot='A',depart=0,arrival=2,end=4),dict(lot='B',depart=2,arrival=4,end=6)]
    return [task('repair_transport_capacity',wrong=bad,repaired=good,oracle_arrivals=[2,4]),
            task('repair_transport_time_units',wrong_first_arrival=120,repaired=good,unit='minutes',boundary='Single predeclared two-minute route; no hidden graph routing or real AMHS model')]

def alarm_diagnosis():
    trace=np.r_[np.zeros(40),np.ones(40)*5];cuts=rpt.Pelt(model='l2',min_size=5,jump=1).fit(trace).predict(pen=3);assert cuts==[40,80]
    alarms=[roundtrip(sf.SecsS05F01,{'ALCD':code,'ALID':25,'ALTX':'pressure'})[0] for code in [129,1]]
    assert alarms[0]['ALCD']&128 and not alarms[1]['ALCD']&128
    alarm_time=40;wrong_time=alarm_time*60;assert abs(wrong_time-cuts[0])>1 and abs(alarm_time-cuts[0])<=1
    intervals=[dict(alarm_id=25,start=40,end=50)];assert intervals[0]['end']-intervals[0]['start']==10
    return [task('repair_alarm_trace_clock',breakpoints=cuts,wrong_alarm_time=wrong_time,repaired_time=40,oracle_shift_index=40),
            task('separate_alarm_set_clear',decoded=alarms,active_intervals=intervals,oracle_active_duration=10,boundary='Time association, not causal root-cause proof')]

def predictive_run(threshold):
    detector=PageHinkley(min_instances=5,delta=.01,threshold=threshold,alpha=1);alarm=None
    for idx,value in enumerate([0]*100+[5]*80):
        detector.update(value)
        if detector.drift_detected and alarm is None:alarm=idx
    env=simpy.Environment();rows=[]
    def equipment():
        if alarm is not None and alarm<150:
            yield env.timeout(alarm);rows.append(dict(time=env.now,event='PM_start'));yield env.timeout(2);rows.append(dict(time=env.now,event='PM_end'))
        else:
            yield env.timeout(150);rows.append(dict(time=env.now,event='failure'));yield env.timeout(20);rows.append(dict(time=env.now,event='recovered'))
    env.process(equipment());env.run();return alarm,rows,rows[-1]['time']-rows[0]['time']
def predictive_maintenance():
    bad,blog,bd=predictive_run(1e9);good,glog,gd=predictive_run(10);assert bad is None and bd==20 and 100<=good<=105 and gd==2
    model=Weibull_Distribution(alpha=200,beta=2);wrong=float(model.SF(xvals=9000,show_plot=False));correct=float(model.SF(xvals=150,show_plot=False));oracle=math.exp(-(150/200)**2)
    assert wrong==0 and math.isclose(correct,oracle,rel_tol=1e-12)
    return [task('repair_predictive_trigger',wrong_downtime=bd,repaired_alarm_index=good,repaired_downtime=gd,log=glog,boundary='Policy assumes timely PM averts a predeclared failure; no measured RUL/policy benefit claim'),
            task('repair_lifetime_clock_units',wrong_survival=wrong,repaired_survival=correct,oracle=oracle,unit='hours')]

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--only');args=parser.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    funcs={'06.01.01':protocol,'06.01.02':events_alarms,'06.01.03':remote_recipe,'06.01.04':opcua,'06.01.05':canonical_events,
           '06.04.01':dynamics,'06.04.02':feedback,'06.04.03':mpc_tasks,'06.04.04':mhe_tasks,'06.05.02':factory_components,
           '06.05.06':general_schedule,'06.05.07':amhs,'06.06.01':alarm_diagnosis,'06.06.05':predictive_maintenance}
    for sid,fn in funcs.items():
        if args.only and sid not in args.only.split(','):continue
        print('running',sid,flush=True);first=fn();second=fn()
        # Nonlinear solver numerical rounding is tolerance-tested within each task.
        def rounded(v):
            if isinstance(v,float):return round(v,7)
            if isinstance(v,list):return [rounded(x) for x in v]
            if isinstance(v,dict):return {k:rounded(x) for k,x in v.items()}
            return v
        assert rounded(first)==rounded(second)
        report=dict(scenario_id=sid,status='passed',tasks=first,repeat_runs=2,repeat_equal=True,repeat_comparison='recursive round7 for numerical solver results, exact for discrete fields',versions={p:importlib.metadata.version(p) for p in ['secsgem','asyncua','control','do-mpc','ortools','simpy','river','ruptures','reliability','pandas']},boundaries=['Only localhost/byte fixtures and artificial dynamics/factory loads; no external equipment.','Recipe policy, canonical state and factory routing are explicit engineering model code.','All selected APIs are not exhaustively executed.'])
        (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(sid,'passed',len(first),flush=True)

if __name__=='__main__':main()

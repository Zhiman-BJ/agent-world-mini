"""QCoDeS acquisition and persistence against explicit analytic virtual DUTs.

These fixtures test measurement strategy/state/units. They are not hardware
drivers, TCAD, or validated compact models of a particular semiconductor.
"""
from __future__ import annotations
from importlib.metadata import version
import json
import math
from pathlib import Path
import tempfile

import numpy as np
from qcodes.instrument import Instrument
from qcodes.parameters import Parameter
from qcodes.validators import Numbers
from qcodes.dataset import initialise_or_create_database_at, load_or_create_experiment, Measurement, load_by_id

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/measurement'


def acquire(exp, x, y, xs, name):
    measurement=Measurement(exp=exp,name=name)
    measurement.register_parameter(x)
    measurement.register_parameter(y,setpoints=(x,))
    values=[]
    with measurement.run() as saver:
        for value in xs:
            x(value)
            measured=y()
            saver.add_result((x,value),(y,measured))
            values.append(float(measured))
        saver.flush_data_to_database()
        run_id=saver.run_id
    loaded=load_by_id(run_id)
    persisted=loaded.get_parameter_data(y.full_name)[y.full_name]
    assert np.allclose(persisted[x.full_name],xs,rtol=0,atol=1e-14)
    # SQLite's numeric serialization can round at the last binary64 bits.
    assert np.allclose(persisted[y.full_name],values,rtol=2e-14,atol=1e-25)
    assert loaded.completed and loaded.number_of_results==len(xs)
    return np.array(values),run_id


def fit_threshold(vg,current):
    slope,intercept=np.polyfit(vg,np.sqrt(current),1)
    return float(-intercept/slope),float(slope*slope)


def dc(exp):
    smu=Instrument('virtual_smu')
    try:
        smu.add_parameter('vg',unit='V',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=0,vals=Numbers(0,1.5))
        smu.add_parameter('compliance',unit='A',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=0.001,vals=Numbers(1e-8,0.01))
        smu.add_parameter('current',unit='A',parameter_class=Parameter,set_cmd=False,
                          get_cmd=lambda:min(0.001*max(float(smu.vg())-0.4,0)**2,float(smu.compliance())))
        vg=np.array([0.5,0.7,0.9,1.1,1.3])
        normal,nrun=acquire(exp,smu.vg,smu.current,vg,'iv_normal')
        expected=np.array([10,90,250,490,810])*1e-6
        assert np.allclose(normal,expected,rtol=0,atol=1e-18)
        threshold,beta=fit_threshold(vg,normal)
        assert abs(threshold-0.4)<1e-12 and abs(beta-0.001)<1e-15
        gm=float((normal[3]-normal[1])/(vg[3]-vg[1]))
        assert abs(gm-0.001)<1e-15
        smu.compliance(100e-6)
        wrong,wrun=acquire(exp,smu.vg,smu.current,vg,'iv_compliance_wrong')
        bad_threshold,_=fit_threshold(vg,wrong)
        assert abs(bad_threshold-0.4)>0.1 and not np.allclose(wrong,expected,rtol=0,atol=1e-18)
        smu.compliance(0.001)
        repaired,rrun=acquire(exp,smu.vg,smu.current,vg,'iv_compliance_repaired')
        assert np.array_equal(repaired,normal)
        try:
            smu.vg(5)
        except ValueError as exc:
            range_error=str(exc)
        else:
            raise AssertionError('Out-of-range bias was accepted')
        smu.vg(0)
        assert smu.vg()==0
        return [
            dict(id='measure_threshold_and_transconductance',status='passed',vg_V=vg.tolist(),current_A=normal.tolist(),threshold_V=threshold,beta_A_per_V2=beta,gm_at_0_9V=gm,run=nrun),
            dict(id='repair_compliance_and_safe_bias',status='passed',wrong_threshold_V=bad_threshold,wrong_current_A=wrong.tolist(),repaired_current_A=repaired.tolist(),range_error=range_error,runs=[wrun,rrun]),
        ]
    finally:
        smu.close()


def cv(exp):
    lcr=Instrument('virtual_lcr')
    try:
        lcr.add_parameter('reverse_bias',unit='V',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=0,vals=Numbers(0,5))
        lcr.add_parameter('frequency',unit='Hz',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=1000000,vals=Numbers(1000,1000000))
        # Explicit teaching fixture: extra low-frequency capacitance is 10pF.
        lcr.add_parameter('capacitance',unit='F',parameter_class=Parameter,set_cmd=False,
                          get_cmd=lambda:100e-12/math.sqrt(1+float(lcr.reverse_bias())/0.8)+(10e-12 if float(lcr.frequency())<100000 else 0))
        vr=np.array([0,0.2,0.8,1.6,3.2])
        normal,nrun=acquire(exp,lcr.reverse_bias,lcr.capacitance,vr,'cv_normal')
        # Independent linearized depletion oracle; x=V/Vbi, y=(C0/C)^2=1+x.
        y=(100e-12/normal)**2
        assert np.allclose(y,[1,1.25,2,3,5],rtol=0,atol=2e-14)
        slope,intercept=np.polyfit(vr,y,1)
        built_in=1/slope
        assert abs(built_in-0.8)<1e-12 and abs(intercept-1)<1e-12
        lcr.frequency(10000)
        wrong,wrun=acquire(exp,lcr.reverse_bias,lcr.capacitance,vr,'cv_frequency_wrong')
        assert np.max(np.abs((100e-12/wrong)**2-np.array([1,1.25,2,3,5])))>1
        lcr.frequency(1000000)
        repaired,rrun=acquire(exp,lcr.reverse_bias,lcr.capacitance,vr,'cv_frequency_repaired')
        assert np.array_equal(normal,repaired)
        # Treating farads as pF without conversion is explicitly rejected.
        assert not math.isclose(float(normal[0]),100,abs_tol=1e-9)
        assert math.isclose(float(normal[0])*1e12,100,abs_tol=1e-9)
        return [
            dict(id='acquire_depletion_cv',status='passed',reverse_bias_V=vr.tolist(),capacitance_F=normal.tolist(),built_in_potential_V=float(built_in),linearized=y.tolist(),run=nrun),
            dict(id='repair_cv_frequency_and_units',status='passed',wrong_capacitance_F=wrong.tolist(),repaired_capacitance_F=repaired.tolist(),frequency_Hz=1000000,zero_bias_pF=100,runs=[wrun,rrun]),
        ]
    finally:
        lcr.close()


def stress(exp):
    dut=Instrument('virtual_stress_dut')
    try:
        dut.add_parameter('stress_seconds',unit='s',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=0,vals=Numbers(0,10000))
        dut.add_parameter('temperature',unit='K',parameter_class=Parameter,get_cmd=None,set_cmd=None,initial_value=300,vals=Numbers(250,400))
        # Deliberately specified virtual law, not a calibrated BTI physical model.
        dut.add_parameter('threshold',unit='V',parameter_class=Parameter,set_cmd=False,
                          get_cmd=lambda:0.4+0.01*float(dut.stress_seconds())**0.25*(float(dut.temperature())/300))
        times=np.array([0,1,16,81,256])
        normal,nrun=acquire(exp,dut.stress_seconds,dut.threshold,times,'stress_normal')
        assert np.allclose(normal,[0.4,0.41,0.42,0.43,0.44],rtol=0,atol=1e-14)
        dut.temperature(360)
        high,hrun=acquire(exp,dut.stress_seconds,dut.threshold,times,'stress_hot')
        assert np.allclose(high,[0.4,0.412,0.424,0.436,0.448],rtol=0,atol=1e-14)
        wrong,wrun=acquire(exp,dut.stress_seconds,dut.threshold,times,'stress_target_wrong')
        assert abs(float(wrong[-1])-0.43)>0.01
        # Target +30mV at 360K. Algebraically independent inverse selects 39.0625s.
        chosen_time=39.0625
        repaired,rrun=acquire(exp,dut.stress_seconds,dut.threshold,[chosen_time],'stress_target_repaired')
        assert abs(float(repaired[0])-0.43)<1e-14
        try:
            dut.temperature(500)
        except ValueError as exc:
            range_error=str(exc)
        else:
            raise AssertionError('Invalid temperature accepted')
        dut.temperature(300)
        dut.stress_seconds(0)
        assert dut.threshold()==0.4
        return [
            dict(id='measure_temperature_stress_series',status='passed',stress_seconds=times.tolist(),threshold_300K_V=normal.tolist(),threshold_360K_V=high.tolist(),runs=[nrun,hrun]),
            dict(id='adapt_stress_to_fixed_drift',status='passed',wrong_final_threshold_V=float(wrong[-1]),target_threshold_V=0.43,repaired_stress_seconds=chosen_time,repaired_threshold_V=float(repaired[0]),temperature_range_error=range_error,reset_threshold_V=0.4,runs=[wrun,rrun]),
        ]
    finally:
        dut.close()


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('qcodes')=='0.59.0'
    work=Path(tempfile.mkdtemp(prefix='acquisition_',dir=OUT))
    database=work/'measurements.db'
    initialise_or_create_database_at(database)
    exp=load_or_create_experiment(experiment_name='fixed_virtual_semiconductor',sample_name='manufactured_fixture')
    for sid,verify in [('04.01.01',dc),('04.01.02',cv),('04.01.03',stress)]:
        report=dict(scenario_id=sid,status='passed',versions={'qcodes':version('qcodes')},tasks=verify(exp),
                    database_path=database.relative_to(Path.cwd()).as_posix(),
                    oracle_independence='固定电流/线性化CV/应力阈值表和目标工作点；错误配置与修复使用相同目标和容差，测量数据从真实QCoDeS SQLite数据集重载核验。',
                    boundaries=['使用脚本中明确声明的解析虚拟DUT，非真实SMU/LCR/B1500或实测器件。','没有通过文件记录调用假装完成硬件测量；运行了真实Parameter校验、Measurement采集、DataSaver和SQLite重载。','本样例不声称TCAD、陷阱动力学、温漂/噪声或特定半导体紧凑模型有效性。'])
        (OUT/f'{sid}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(sid,[(t['id'],t['status']) for t in report['tasks']])


if __name__=='__main__':
    main()

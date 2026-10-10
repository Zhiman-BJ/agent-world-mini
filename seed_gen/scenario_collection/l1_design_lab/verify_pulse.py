"""Offline pulse compilation/output simulation and toolkit waveform bridge."""
from importlib.metadata import version
import json
from pathlib import Path
import numpy as np

from laboneq.simple import (DeviceSetup,HDAWG,create_connection,Session,Experiment,ExperimentSignal,
                           Calibration,SignalCalibration,Oscillator,ModulationType,OutputSimulator)
from laboneq.dsl.experiment.pulse import PulseFunctional
from zhinst.toolkit import Waveforms

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/pulse'


def compile_pulse(session,setup,amplitude):
    pulse=PulseFunctional(function='const',uid='rectangle',length=80e-9,amplitude=amplitude)
    exp=Experiment(uid='fixed_awg_rectangle',signals=[ExperimentSignal('drive')])
    exp.set_signal_map({'drive':setup.logical_signal_by_uid('q0/drive')})
    with exp.acquire_loop_rt(count=1):
        with exp.section(uid='pulse'):
            exp.play(signal='drive',pulse=pulse)
    compiled=session.compile(exp)
    simulator=OutputSimulator(compiled)
    channel=setup.logical_signal_by_uid('q0/drive').physical_channel
    snippet=simulator.get_snippet(channel,start=0,output_length=1e-6)
    wave=np.asarray(snippet.wave)
    dt=float(np.median(np.diff(snippet.time)))
    # I/Q output sample-precise waveform, no qubit measurement simulation.
    return pulse,compiled,wave,dt


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('laboneq')=='26.7.0' and version('zhinst-toolkit')=='1.4.0'
    setup=DeviceSetup(uid='offline_hdawg')
    setup.add_dataserver(host='localhost',port=8004)
    setup.add_instruments(HDAWG(uid='hdawg',address='DEV8001',device_options='HDAWG8'))
    setup.add_connections('hdawg',create_connection(to_signal='q0/drive',ports=['SIGOUTS/0','SIGOUTS/1'],type='iq'))
    setup.set_calibration(Calibration({'q0/drive':SignalCalibration(oscillator=Oscillator(uid='zero_if',frequency=0,modulation_type=ModulationType.SOFTWARE),range=1.0)}))
    session=Session(setup,log_level=40)
    session.connect(do_emulation=True)
    try:
        normal,compiled,wave,dt=compile_pulse(session,setup,0.4)
        active=wave[np.abs(wave)>1e-12]
        assert len(active)>0
        amplitude=float(np.max(np.abs(active)))
        duration=float(len(active)*dt)
        assert abs(amplitude-0.4)<1e-8 and abs(duration-80e-9)<1e-14,(amplitude,duration,dt)
        _,_,bad_wave,bad_dt=compile_pulse(session,setup,0.2)
        bad_area=float(np.sum(np.abs(bad_wave))*bad_dt)
        target_area=32e-9
        assert abs(bad_area-target_area)>1e-9
        _,_,fixed_wave,fixed_dt=compile_pulse(session,setup,0.4)
        fixed_area=float(np.sum(np.abs(fixed_wave))*fixed_dt)
        assert abs(fixed_area-target_area)<1e-14
        t,samples=normal.generate_sampled_pulse(sampling_rate=2.4e9)
        assert len(samples)==192 and np.max(abs(samples-0.4))<1e-12
        toolkit=Waveforms()
        toolkit.assign_waveform(0,samples.real,samples.imag)
        raw=toolkit.get_raw_vector(0)
        restored=Waveforms()
        restored.assign_native_awg_waveform(0,raw,channels=2)
        raw_again=restored.get_raw_vector(0)
        assert np.array_equal(raw,raw_again)
        decoded_i,decoded_q,_=restored[0]
        quantization_error=float(np.max(np.abs(decoded_i-0.4)))
        assert quantization_error<=1/32767 and np.max(abs(decoded_q))==0
        wrong=PulseFunctional(function='const',uid='wrong_sample_clock',length=80e-9,amplitude=0.4)
        _,wrong_samples=wrong.generate_sampled_pulse(sampling_rate=2e9)
        assert len(wrong_samples)==160 and len(wrong_samples)!=192
        np.savez(OUT/'waveforms.npz',wave=wave,dt=dt,sampled=samples,native_awg=raw,repaired=fixed_wave)
        report=dict(scenario_id='04.02.01',status='passed',versions={p:version(p) for p in ['laboneq','zhinst-toolkit']},
                    tasks=[dict(id='compile_and_verify_awg_pulse',status='passed',amplitude=amplitude,duration_seconds=duration,sample_dt_seconds=dt,active_samples=len(active),area_seconds=fixed_area),
                           dict(id='repair_pulse_amplitude_and_sample_clock',status='passed',wrong_area_seconds=bad_area,target_area_seconds=target_area,repaired_area_seconds=fixed_area,wrong_sample_count=len(wrong_samples),repaired_sample_count=len(samples),toolkit_quantization_error=quantization_error,native_roundtrip_equal=True)],
                    oracle_independence='固定矩形80ns×0.4=32ns面积、HDAWG2.4GSa/s→192点与16bit量化≤1/32767；真实编译OutputSimulator波形和Toolkit打包/解包同一目标验收。',
                    boundaries=['Session明确do_emulation=True；未连接数据服务器或硬件，不声称实际仪器校准/量子比特Rabi结果。','OutputSimulator测试编译后的AWG输出样本；无真实示波器测量。','pulse_library常用工厂由装饰器动态生成，使用源码PulseFunctional生成构造+采样，不错误调用AST采样器签名。'])
        (OUT/'04.02.01.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        session.disconnect()


if __name__=='__main__':
    main()

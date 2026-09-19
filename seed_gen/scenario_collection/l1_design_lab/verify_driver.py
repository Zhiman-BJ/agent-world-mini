"""Run an actual contributed VNA driver against its official simulated backend."""
import hashlib
from importlib.metadata import version
from importlib.resources import files
import json
from pathlib import Path

from qcodes_contrib_drivers.drivers.Keysight.Keysight_E5080B import KeySight_E5080B

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/driver'


def settings(vna):
    names=['start_freq','stop_freq','points','source_power','if_bandwidth','sweep_type','scattering_parameter','rf_on']
    return {name:vna.parameters[name]() for name in names}


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert version('qcodes-contrib-drivers')=='0.25.0'
    yaml=files('qcodes_contrib_drivers.sims')/'Keysight_E5080B.yaml'
    source=Path('seed_pypi_raw/l1_design_lab/qcodes-contrib-drivers/src/qcodes_contrib_drivers/sims/Keysight_E5080B.yaml')
    assert yaml.read_bytes()==source.read_bytes()
    vna=KeySight_E5080B('fixture_vna',address='TCPIP::192.168.0.10::INSTR',pyvisa_sim_file='qcodes_contrib_drivers.sims:Keysight_E5080B.yaml')
    try:
        assert vna.get_idn()['model']=='Keysight_E5080B_simulated'
        vna.sweep_type('LIN')
        vna.start_freq(1e6)
        vna.stop_freq(3e6)
        vna.points(11)
        vna.source_power(-20)
        vna.if_bandwidth(1000)
        vna.scattering_parameter('S21')
        vna.rf_on(False)
        target=dict(start_freq=1e6,stop_freq=3e6,points=11,source_power=-20.0,if_bandwidth=1000.0,sweep_type='LIN',scattering_parameter='S21',rf_on=False)
        normal=settings(vna)
        assert normal==target
        # Each parameter is legal individually, but the combined sweep is wrong.
        vna.start_freq(4e6)
        wrong=settings(vna)
        assert wrong['start_freq']>=wrong['stop_freq'] and wrong!=target
        vna.start_freq(1e6)
        fixed=settings(vna)
        assert fixed==target
        errors={}
        for name,value in [('source_power',30),('sweep_type','BAD'),('start_freq',500)]:
            try:
                vna.parameters[name](value)
            except ValueError as exc:
                errors[name]=str(exc)
            else:
                raise AssertionError(f'{name} accepted invalid value')
        assert settings(vna)==target
        report=dict(scenario_id='04.03.01',status='passed',versions={p:version(p) for p in ['qcodes-contrib-drivers','qcodes','pyvisa','pyvisa-sim']},
                    official_simulation_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    tasks=[dict(id='configure_contributed_vna_driver',status='passed',target=target,readback=normal),
                           dict(id='repair_vna_sweep_configuration',status='passed',wrong=wrong,repaired=fixed,range_and_enum_errors=errors)],
                    oracle_independence='固定1–3MHz/11点/-20dBm/1kHz/S21配置表和start<stop不变量。驱动仅分别验证频率范围，因此错误组合由独立场景verifier检出；恢复后完整表逐项相等。',
                    boundaries=['使用真实发布版KeySight_E5080B驱动与官方原样YAML，192.168.0.10只是模拟资源名，没有连接该真实IP。','本任务是驱动配置/范围/错误恢复，未读取S参数、校准或仿真RF电路；官方测试get_data以monkeypatch替换query，本脚本不据此声称数据采集。','驱动模块本身导入时等待5秒，未修改官方源码。'])
        (OUT/'04.03.01.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        vna.close()


if __name__=='__main__':
    main()

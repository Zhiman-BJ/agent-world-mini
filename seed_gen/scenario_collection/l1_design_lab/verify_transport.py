"""Exercise real VISA TCP sockets and real PyVISA-sim/QCoDeS integration."""
from __future__ import annotations
from importlib.metadata import version
import json
from pathlib import Path
import socketserver
import threading

import pyvisa
from pyvisa.errors import VisaIOError, InvalidSession
from qcodes.instrument import VisaInstrument
from qcodes.validators import Numbers, Enum

BASE=Path(__file__).resolve().parent
OUT=BASE/'runtime/transport'


class LoopbackSCPI(socketserver.StreamRequestHandler):
    """Explicit in-process fixture; real TCP transport, no external hardware."""
    def handle(self):
        voltage=0.0
        while line:=self.rfile.readline():
            command=line.decode('ascii').strip()
            self.server.commands.append(command)
            if command=='*IDN?':
                response='SeedLab,SocketFixture,FIXTURE02,1.0'
            elif command=='VOLT?':
                response=f'{voltage:.6f}'
            elif command.startswith('VOLT '):
                value=float(command.split()[1])
                if 0<=value<=1.5:
                    voltage=value
                continue
            elif command=='READ:ARRAY?':
                response='0.100,0.400,0.900'
            else:
                response='ERROR'
            self.wfile.write((response+'\n').encode('ascii'))
            self.wfile.flush()


def transport():
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),LoopbackSCPI)
    server.daemon_threads=True
    server.commands=[]
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    manager=pyvisa.ResourceManager('@py')
    address=f'TCPIP::127.0.0.1::{server.server_address[1]}::SOCKET'
    try:
        instrument=manager.open_resource(address,read_termination='\n',write_termination='\n',timeout=500)
        assert instrument.query('*IDN?')=='SeedLab,SocketFixture,FIXTURE02,1.0'
        observed=[]
        for voltage in [0.2,0.6,1.2]:
            instrument.write(f'VOLT {voltage}')
            observed.append(float(instrument.query('VOLT?')))
        assert observed==[0.2,0.6,1.2]
        assert instrument.query_ascii_values('READ:ARRAY?')==[0.1,0.4,0.9]
        instrument.close()
        try:
            _=instrument.session
        except InvalidSession as exc:
            closed_error=str(exc)
        else:
            raise AssertionError('Closed session remained usable')
        wrong=manager.open_resource(address,read_termination=None,write_termination='\n',timeout=120)
        try:
            wrong.query('*IDN?')
        except VisaIOError as exc:
            termination_error=str(exc)
        else:
            raise AssertionError('Missing terminator should fail on persistent socket')
        wrong.close()
        fixed=manager.open_resource(address,read_termination='\n',write_termination='\n',timeout=500)
        idn=fixed.query('*IDN?')
        assert idn=='SeedLab,SocketFixture,FIXTURE02,1.0'
        fixed.write('VOLT 1.2')
        assert float(fixed.query('VOLT?'))==1.2
        fixed.close()
        return dict(scenario_id='04.04.01',status='passed',versions={p:version(p) for p in ['pyvisa','pyvisa-py']},
                    tasks=[dict(id='visa_socket_acquisition',status='passed',observed_voltage_V=observed,ascii_array=[0.1,0.4,0.9],closed_session_error=closed_error),
                           dict(id='repair_visa_termination',status='passed',wrong_termination_error=termination_error,repaired_idn=idn,repaired_voltage_V=1.2)],
                    backend_info=manager.visalib.get_debug_info(),commands=server.commands,
                    oracle_independence='固定SCPI identity、三点电压回读目标和三点ASCII数组；错误终止符发生真实TCP超时，修复后重开资源按同一目标验收。',
                    boundaries=['真实pyvisa-py TCPIP SOCKET，但服务端是脚本中显式定义的本地fixture。','没有访问USB/GPIB/实体仪器或扫描外部网络。','VISA细节应在后续Agent facade内封装；该L3是环境基础设施种子。'])
    finally:
        manager.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def simulated():
    yaml=BASE/'fixtures/virtual_smu.yaml'
    backend=str(yaml)+'@sim'
    smu=VisaInstrument('simulated_smu','TCPIP::127.0.0.1::INSTR',visalib=backend,terminator='\n',device_clear=False)
    try:
        smu.add_parameter('voltage',unit='V',set_cmd='VOLT {:.6f}',get_cmd='VOLT?',get_parser=float,vals=Numbers(0,2))
        smu.add_parameter('output',set_cmd='OUTP {:d}',get_cmd='OUTP?',get_parser=int,vals=Enum(0,1))
        assert smu.ask('*IDN?')=='SeedLab,VirtualSMU,FIXTURE01,1.0'
        smu.output(1)
        voltages=[]
        for value in [0.2,0.6,1.2]:
            smu.voltage(value)
            voltages.append(smu.voltage())
        assert voltages==[0.2,0.6,1.2] and smu.output()==1
        assert smu.ask('*ESR?')=='0'
        # Client validator deliberately wider than backend; device rejects 1.8V.
        smu.voltage(1.8)
        error_status=int(smu.ask('*ESR?'))
        actual=smu.voltage()
        assert error_status&32 and actual==1.2
        smu.voltage.vals=Numbers(0,1.5)
        try:
            smu.voltage(1.8)
        except ValueError as exc:
            client_error=str(exc)
        else:
            raise AssertionError('Repaired client bounds accepted invalid value')
        smu.voltage(1.0)
        assert smu.voltage()==1.0 and smu.ask('*ESR?')=='0'
        smu.output(0)
        smu.voltage(0)
        assert smu.output()==0 and smu.voltage()==0
        return dict(scenario_id='04.04.02',status='passed',versions={p:version(p) for p in ['qcodes','pyvisa','pyvisa-sim']},
                    tasks=[dict(id='virtual_instrument_bias_sequence',status='passed',voltages_V=voltages,output_enabled=1,backend='PyVISA-sim YAML'),
                           dict(id='repair_virtual_driver_range',status='passed',backend_error_status=error_status,stale_setpoint_actual_V=actual,client_range_error=client_error,repaired_voltage_V=1.0,safe_final_voltage_V=0,output_disabled=True)],
                    backend_info=smu.resource_manager.visalib.get_debug_info(),
                    oracle_independence='YAML设备的0–1.5V合法范围、已知set/read序列与ESR位32目标；客户端2V范围故意不同，必须修复为设备限制且重新读取实际值。',
                    boundaries=['真实QCoDeS→PyVISA→PyVISA-sim桥接，无真实SMU。','YAML只模拟命令/属性/错误状态，不生成半导体I-V物理响应。','没有将客户端set缓存视为设备实际状态，读回必须确认被拒绝写入未生效。'])
    finally:
        smu.close()


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    assert {p:version(p) for p in ['pyvisa','pyvisa-py','pyvisa-sim','qcodes']}=={'pyvisa':'1.16.2','pyvisa-py':'0.8.1','pyvisa-sim':'0.7.0','qcodes':'0.59.0'}
    for verify in [transport,simulated]:
        report=verify()
        (OUT/(report['scenario_id']+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(report['scenario_id'],[(t['id'],t['status']) for t in report['tasks']])


if __name__=='__main__':
    main()

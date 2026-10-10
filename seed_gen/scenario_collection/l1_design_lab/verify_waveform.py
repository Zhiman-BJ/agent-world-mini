"""Run real pyvcd -> VCD -> vcdvcd fixtures, without claiming RTL simulation."""
from __future__ import annotations
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path

from vcd.writer import VCDWriter, VCDPhaseError
from vcd.reader import tokenize, TokenKind
from vcd.gtkw import GTKWSave
from vcdvcd import VCDVCD

BASE = Path(__file__).resolve().parent
OUT = BASE/'runtime/waveform'
ORACLE = [(0, 0), (10, 1), (20, 2), (30, 3), (40, 0), (50, 1)]


def write_trace(path, *, fault=False, timescale='1 ns'):
    with path.open('w', encoding='ascii') as stream:
        writer = VCDWriter(stream, timescale=timescale, date='fixed fixture', comment='not an RTL simulator')
        counter = writer.register_var('dut', 'count', 'wire', size=2, init=0)
        writer.register_alias('monitor', 'count', counter)
        enable = writer.register_var('dut', 'enable', 'wire', size=1, init=1)
        for t, n in ORACLE:
            writer.change(counter, t, 2 if fault and t == 30 else n)
        writer.change(enable, 60, 0)
        writer.close(60)


def read_values(path):
    vcd = VCDVCD(str(path))
    signal = vcd.data[vcd.references_to_ids['dut.count']]
    assert signal.size == '2'
    assert vcd.references_to_ids['monitor.count'] == vcd.references_to_ids['dut.count']
    # Direct returned attributes are the documented, non-deprecated API.
    actual = []
    for timestamp, expected in ORACLE:
        values = [value for t, value in signal.tv if t <= timestamp]
        actual.append((timestamp, int(values[-1], 2)))
    return actual, float(vcd.timescale['timescale'])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    versions = {n: version(n) for n in ['pyvcd', 'vcdvcd']}
    assert versions == {'pyvcd': '0.5.0', 'vcdvcd': '2.6.0'}
    for stem, kwargs in [('normal', {}), ('wrong_value', {'fault': True}), ('repaired', {}), ('wrong_scale', {'timescale':'1 us'})]:
        write_trace(OUT/f'{stem}.vcd', **kwargs)
    normal, scale = read_values(OUT/'normal.vcd')
    wrong, _ = read_values(OUT/'wrong_value.vcd')
    fixed, _ = read_values(OUT/'repaired.vcd')
    mismatch = next(t for (t, actual), (_, expected) in zip(wrong, ORACLE) if actual != expected)
    assert normal == ORACLE and fixed == ORACLE and mismatch == 30
    assert (OUT/'normal.vcd').read_bytes() == (OUT/'repaired.vcd').read_bytes()
    _, bad_scale = read_values(OUT/'wrong_scale.vcd')
    assert abs(10*scale-1e-8) < 1e-20
    assert abs(10*bad_scale-1e-8) > 1e-8
    with (OUT/'normal.vcd').open('rb') as stream:
        tokens = list(tokenize(stream))
    assert next(t.timescale for t in tokens if t.kind == TokenKind.TIMESCALE).unit.value == 'ns'
    assert len([t for t in tokens if t.kind == TokenKind.VAR]) == 3
    assert len([t for t in tokens if t.kind == TokenKind.CHANGE_TIME]) == 7
    with (OUT/'debug.gtkw').open('w') as stream:
        save = GTKWSave(stream)
        save.dumpfile('normal.vcd', abspath=False)
        save.timestart(20)
        save.zoom_markers(marker=30)
        save.trace('dut.count', datafmt='dec')
        save.trace('dut.enable')
    assert 'dut.count' in (OUT/'debug.gtkw').read_text()
    writer = VCDWriter(io.StringIO())
    var = writer.register_var('dut', 'q', 'wire', size=1)
    writer.change(var, 10, 1)
    try:
        writer.change(var, 5, 0)
    except VCDPhaseError as exc:
        chronological_error = str(exc)
    else:
        raise AssertionError('Backwards timestamp was accepted')
    report = {
        'scenario_id':'03.08.01', 'status':'passed', 'versions':versions,
        'tasks':[
            {'id':'locate_and_repair_counter_divergence', 'status':'passed', 'first_divergence_ns':mismatch,
             'normal':normal, 'wrong':wrong, 'repaired':fixed, 'oracle':ORACLE},
            {'id':'repair_trace_units_and_order', 'status':'passed', 'target_first_edge_seconds':1e-8,
             'wrong_first_edge_seconds':10*bad_scale, 'repaired_first_edge_seconds':10*scale, 'backwards_time_error':chronological_error},
        ],
        'artifact_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.glob('*')) if p.suffix in ('.vcd','.gtkw')},
        'oracle_independence':'固定6项手写模4计数表、固定首跳变10ns目标，与被测writer/parser实现无关；按同一目标拒绝错误值/时间单位并验收修复。',
        'boundaries':['只有波形文件生成/解析/诊断，未运行Verilog DUT或GTKWave GUI。', '正常、错误、修复三类文件是明确制造的数字波形fixture。'],
    }
    destination = OUT/'03.08.01.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

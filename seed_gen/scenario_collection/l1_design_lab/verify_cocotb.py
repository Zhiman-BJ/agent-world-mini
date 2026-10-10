"""WSL runner: real Icarus build and three Python hardware verification suites."""
from importlib.metadata import version
import os
from pathlib import Path
from cocotb_tools.runner import get_runner

BASE=Path(__file__).resolve().parent
PREFIX=Path('/home/zjs32/.local/share/semiconductor-design-lab-20260917')


def main():
    os.environ['PATH']=str(PREFIX/'iverilog/usr/bin')+os.pathsep+os.environ['PATH']
    for name,expected in [('cocotb','2.1.0'),('pyuvm','5.0.0'),('cocotbext-axi','0.1.28'),('cocotb-bus','0.3.0')]:
        assert version(name)==expected
    out=BASE/'runtime/cocotb'
    out.mkdir(parents=True,exist_ok=True)
    runner=get_runner('icarus')
    runner.build(sources=[BASE/'fixtures/verification_dut.sv'],hdl_toplevel='verification_dut',build_dir=out/'build',build_args=['-B',str(PREFIX/'iverilog/usr/lib/x86_64-linux-gnu/ivl')],always=True)
    runner.test(hdl_toplevel='verification_dut',test_module='test_hardware_verification',test_dir=BASE,results_xml=out/'results.xml',log_file=out/'simulation.log')


if __name__=='__main__':
    main()

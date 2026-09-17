#!/usr/bin/env bash
set -euo pipefail
dft_prefix=/home/zjs32/.local/share/semiconductor-dft-20260917
# The pinned release supplies a pure-Python PAW/PW implementation. This path
# deliberately exercises that official implementation, without compiling C.
mkdir -p "$dft_prefix"
python3 -m venv --without-pip "$dft_prefix/env"
"$dft_prefix/env/bin/python" /mnt/d/Desktop/agent-world-mini/.venv-scenario-dft-bootstrap/get-pip.py
"$dft_prefix/env/bin/python" -m pip install 'numpy==2.5.3' 'scipy==1.18.1' 'ase==3.29.0' 'gpaw-data==1.1.0'

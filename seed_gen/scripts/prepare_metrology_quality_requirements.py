"""Snapshot already verified isolated dependencies to tracked lane-level files."""
from pathlib import Path
import subprocess
from urllib.parse import unquote,urlsplit

BASE=Path('seed_gen/scenario_collection/l1_metrology_quality')
UV='C:/Apps/anaconda3/Scripts/uv.exe'
ENVIRONMENTS=[
    ('quality-reliability','reliability-python312'),
    ('metrology-optical','optical-python312'),
    ('quality-graph','quality-graph-python312'),
    ('metrology-microscopy','microscopy-python312'),
    ('metrology-diffraction','diffraction-python312'),
    ('metrology-abtem','abtem-python312'),
    ('metrology-xrd','xrd-python312'),
    ('metrology-py4dstem311','py4dstem-python311'),
]

def main():
    for suffix,label in ENVIRONMENTS:
        interpreter=Path(f'.venv-scenario-{suffix}/Scripts/python.exe')
        assert interpreter.exists(),interpreter
        process=subprocess.run([UV,'pip','freeze','--python',str(interpreter)],check=True,capture_output=True,text=True,encoding='utf-8')
        lines=[]
        for line in process.stdout.splitlines():
            if ' @ file:///' in line:
                name,url=line.split(' @ ',1)
                path=unquote(urlsplit(url).path).lstrip('/')
                relative=Path(path).resolve().relative_to(Path.cwd().resolve())
                line='./'+relative.as_posix()+'  # '+name+'; fixed release checkout, see source manifests'
            lines.append(line)
        output=BASE/f'requirements-{label}.txt'
        output.write_text('# Exact installed versions; run from repository root.\n'+'\n'.join(lines)+'\n',encoding='utf-8')
        print(output,len(lines),flush=True)

if __name__=='__main__':main()

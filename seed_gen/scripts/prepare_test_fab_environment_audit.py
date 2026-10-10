"""Record exact local dependency inventories without changing installations."""
import json
from pathlib import Path
import platform
import subprocess
from seed_gen.scripts.prepare_test_fab_sources import BASE

def main():
    out=BASE/'runtime/environments';out.mkdir(parents=True,exist_ok=True)
    uv='C:/Apps/anaconda3/Scripts/uv.exe'
    for env in ['fab-production','fab-statistics','test-analysis','fab-control']:
        interpreter=Path('.venv-scenario-'+env)/'Scripts/python.exe'
        freeze=subprocess.run([uv,'pip','freeze','--python',str(interpreter)],capture_output=True,text=True,encoding='utf-8',check=True)
        check=subprocess.run([uv,'pip','check','--python',str(interpreter)],capture_output=True,text=True,encoding='utf-8',check=True)
        version=subprocess.run([str(interpreter),'-c','import sys,platform;print(sys.version);print(platform.platform())'],capture_output=True,text=True,encoding='utf-8',check=True)
        (out/(env+'.requirements.txt')).write_text(freeze.stdout,encoding='utf-8')
        (out/(env+'.json')).write_text(json.dumps(dict(environment=str(interpreter.parent.parent),python=version.stdout.strip(),pip_check=(check.stdout+check.stderr).strip(),freeze_file=(out/(env+'.requirements.txt')).as_posix(),status='passed',boundary='Exact installed dependencies; not a lockfile proving identical future platform wheels.'),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(env,'dependency check passed',flush=True)

if __name__=='__main__':main()

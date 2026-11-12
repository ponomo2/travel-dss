"""Install BI dependencies into their own Python 3.11 environment."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info[:2] != (3,11):
        raise SystemExit('Запустите этот скрипт через python3.11')
    target = ROOT/'.superset-venv'
    python = target/'bin/python'
    if not python.exists():
        venv.EnvBuilder(with_pip=True,symlinks=True).create(target)
    version = subprocess.check_output([str(python),'-c','import sys; print("%d.%d" % sys.version_info[:2])'],text=True).strip()
    if version != '3.11':
        raise SystemExit('Существующая .superset-venv использует другой Python. Переименуйте ее и повторите.')
    subprocess.run([str(python),'-m','pip','install','--prefer-binary','-r',str(ROOT/'bi/requirements-superset.lock')],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    core_python = ROOT/'.venv/bin/python'
    if not core_python.exists():
        raise SystemExit('Сначала выполните python3.11 scripts/bootstrap.py для основного приложения')
    subprocess.run([str(core_python),str(ROOT/'scripts/export_bi.py')],check=True)
    subprocess.run([sys.executable,str(ROOT/'scripts/setup_superset.py')],check=True)


if __name__=='__main__': main()

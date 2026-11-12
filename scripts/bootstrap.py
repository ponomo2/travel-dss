"""Create the project environment using the interpreter running this script."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Установка Travel DSS в локальное .venv')
    parser.add_argument('--wheelhouse',type=Path,help='Каталог заранее загруженных колес для установки без сети')
    args = parser.parse_args()
    if not (3,9) <= sys.version_info[:2] < (3,13):
        parser.error('Для фиксированного набора зависимостей нужен Python 3.9–3.12; основной вариант — Python 3.11')
    target = ROOT/'.venv'
    python = target/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if python.exists():
        try:
            version = subprocess.check_output([str(python),'-c','import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")'],text=True,stderr=subprocess.STDOUT).strip()
        except subprocess.CalledProcessError:
            parser.error('Существующая .venv не запускается; переименуйте ее и повторите установку')
        if version != f'{sys.version_info.major}.{sys.version_info.minor}':
            parser.error(f'.venv использует Python {version}; выберите соответствующий интерпретатор либо переименуйте старую .venv')
    else:
        venv.EnvBuilder(with_pip=True,symlinks=os.name != "nt").create(target)
    environment = os.environ.copy()
    environment.pop('PYTHONHOME',None)
    environment.pop('PYTHONPATH',None)
    environment.pop('PYTHONPYCACHEPREFIX',None)
    environment['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    environment['PIP_NO_CACHE_DIR'] = '1'
    pip = [str(python),'-m','pip']
    options = ['--only-binary=:all:']
    if args.wheelhouse:
        options += ['--no-index','--find-links',str(args.wheelhouse.resolve())]
    subprocess.run(pip+['install']+options+['-r',str(ROOT/'requirements.lock')],cwd=ROOT,env=environment,check=True)
    subprocess.run(pip+['install','--no-deps','--no-build-isolation','-e',str(ROOT)],cwd=ROOT,env=environment,check=True)
    subprocess.run(pip+['check'],cwd=ROOT,env=environment,check=True)
    print('\nУстановка завершена. Из корня проекта:')
    print(f'  {python.relative_to(ROOT)} -m travel_dss.cli doctor')
    print(f'  {python.relative_to(ROOT)} -m pytest -q')
    print(f'  {python.relative_to(ROOT)} -m streamlit run app.py --browser.gatherUsageStats false')


if __name__ == '__main__':
    main()

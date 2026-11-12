"""Initialize the local Superset instance without publishing any data."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.runtime/superset'


def main():
    python = ROOT/'.superset-venv/bin/python'
    if not python.exists():
        raise SystemExit('Сначала создайте .superset-venv на Python 3.11 и установите bi/requirements-superset.lock')
    STATE.mkdir(parents=True,exist_ok=True)
    secret = STATE/'secret-key'
    if not secret.exists():
        secret.write_text(secrets.token_urlsafe(48))
        secret.chmod(0o600)
    credentials = STATE/'admin.json'
    if not credentials.exists():
        credentials.write_text(json.dumps({'username':'localadmin','password':secrets.token_urlsafe(24)}))
        credentials.chmod(0o600)
    env = os.environ.copy()
    env['SUPERSET_CONFIG_PATH'] = str(ROOT/'bi/superset_config.py')
    env['SUPERSET_HOME'] = str(STATE)
    env['FLASK_APP'] = 'superset'
    for command in (['db','upgrade'],['init']):
        subprocess.run([str(python),'-c','from superset.cli.main import superset; superset()']+command,cwd=ROOT,env=env,check=True)
    subprocess.run([str(python),str(ROOT/'bi/create_dashboard.py')],cwd=ROOT,env=env,check=True)
    print('Superset настроен локально. Учетные данные: .runtime/superset/admin.json')
    print('Запуск: python scripts/run_superset.py')


if __name__=='__main__': main()

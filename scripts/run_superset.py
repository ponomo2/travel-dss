import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env['SUPERSET_CONFIG_PATH'] = str(ROOT/'bi/superset_config.py')
env['SUPERSET_HOME'] = str(ROOT/'.runtime/superset')
env['FLASK_APP'] = 'superset'
subprocess.run([str(ROOT/'.superset-venv/bin/python'),'-c','from superset.cli.main import superset; superset()','run',
                '-h','127.0.0.1','-p','8088','--with-threads'],cwd=ROOT,env=env,check=True)

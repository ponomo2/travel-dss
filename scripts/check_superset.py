import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
env = {**os.environ,'SUPERSET_CONFIG_PATH':str(ROOT/'bi/superset_config.py'),
       'SUPERSET_HOME':str(ROOT/'.runtime/superset')}
subprocess.run([str(ROOT/'.superset-venv/bin/python'),str(ROOT/'bi/verify_dashboard.py')],
               cwd=ROOT,env=env,check=True)

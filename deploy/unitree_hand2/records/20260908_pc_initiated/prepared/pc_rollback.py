import json,os,pathlib,subprocess
assert os.geteuid()==0
state=json.loads(pathlib.Path('/var/backups/wuji-pc-initiated-20260908/state.json').read_text())
subprocess.run(['systemctl','start' if state['active'] else 'stop','deltacollect-hand-telemetry-tunnel.service'],check=True)
print('PC_PRIOR_ACTIVE_STATE_RESTORED; enabled state unchanged')

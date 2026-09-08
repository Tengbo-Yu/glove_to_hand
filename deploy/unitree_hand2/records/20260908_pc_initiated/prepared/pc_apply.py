import json,os,pathlib,subprocess,urllib.request
assert os.geteuid()==0,'requires PC sudo'
status=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8080/api/status',timeout=3))
assert not any(status.get(x) for x in ['recording','recording_stopping','recording_starting','start_buffering']),'collection active'
service='deltacollect-hand-telemetry-tunnel.service';b=pathlib.Path('/var/backups/wuji-pc-initiated-20260908');assert not b.exists(),'backup exists; inspect prior operation'
active=subprocess.run(['systemctl','is-active','--quiet',service]).returncode==0
enabled=subprocess.check_output(['systemctl','is-enabled',service],text=True).strip();assert enabled=='enabled','unexpected service boot policy'
b.mkdir(mode=0o700);(b/'state.json').write_text(json.dumps({'active':active,'enabled':enabled},indent=2))
subprocess.run(['systemctl','start',service],check=True)
print('PC_TUNNEL_STARTED; verify robot127.0.0.1:6013-6016 before robot_apply')

import pathlib,json,subprocess,base64,hashlib
b=pathlib.Path('/var/backups/wuji-pc-initiated-20260908');old=(b/'wuji-hand2.env').read_bytes();current=pathlib.Path('/etc/default/wuji-hand2').read_bytes()
assert current==old.replace(b'DATA_COLLECTOR_HOST=10.1.10.239',b'DATA_COLLECTOR_HOST=127.0.0.1')
original=json.loads((b/'accepted.json').read_text());now=json.loads(pathlib.Path('/var/lib/deltacollect/config-guard/accepted.json').read_text());changes=[p for p in now['files'] if now['files'][p]!=original['files'].get(p)]
assert changes==['/etc/default/wuji-hand2'],changes
assert base64.b64decode(now['files']['/etc/default/wuji-hand2']['data'])==current
result={'env_only_change':'DATA_COLLECTOR_HOST:10.1.10.239 ->127.0.0.1','accepted_config_changed_paths':changes,'containers':[]}
for side in ['left','right']:
 x=json.loads(subprocess.check_output(['docker','inspect','wuji-hand2-'+side]))[0]
 assert x['State']['Running']
 assert x['Image']=='sha256:2e29e35a5f3cee5b33b9056d0502e562da322a302d5ce4a88eceadf88ea420c4'
 assert 'DATA_COLLECTOR_HOST=127.0.0.1' in x['Config']['Env']
 args=x['Args'];assert args[args.index('--kp')+1]=='6';assert args[args.index('--kd')+1]=='0.2'
 result['containers'].append({'side':side,'running':True,'image':x['Image'],'telemetry_host':'127.0.0.1','kp':6,'kd':0.2,'hand_sn':args[args.index('--hand-sn')+1]})
result['hand_control_connections']=subprocess.check_output(['ss','-tnH','state','established','( sport = :8765 or sport = :8767 )'],text=True).strip()
print(json.dumps(result,indent=2))

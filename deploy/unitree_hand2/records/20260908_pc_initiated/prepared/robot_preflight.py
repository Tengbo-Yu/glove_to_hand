import pathlib,json,base64,subprocess,hashlib,urllib.request
profile=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:5590/v1/hand-profile',timeout=3))
assert profile['profile']['robot_id']=='jetson-1794225001823'
doc=json.loads(pathlib.Path('/var/lib/deltacollect/config-guard/accepted.json').read_text());drift=[]
for name,x in doc['files'].items():
 if pathlib.Path(name).read_bytes()!=base64.b64decode(x['data']):drift.append(name)
assert not drift,drift
assert not subprocess.check_output(['ss','-tnH','state','established','( sport = :8765 or sport = :8767 )'],text=True).strip(),'active hand client'
for side in ['left','right']:
 result=json.loads(subprocess.check_output(['docker','inspect','wuji-hand2-'+side]))[0]
 print(json.dumps({'side':side,'image':result['Image'],'running':result['State']['Running'],'args':result['Args'],'telemetry_env':[v for v in result['Config']['Env'] if v.startswith('DATA_COLLECTOR_HOST=')]}))
print('ROBOT_ID_AND_GUARD_AND_IDLE_VERIFIED')

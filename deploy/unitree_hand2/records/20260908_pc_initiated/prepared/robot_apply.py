"""Root-only guarded transaction. Run only after the PC tunnel is established."""
import base64,hashlib,importlib.util,json,os,pathlib,subprocess,sys,urllib.request
sys.dont_write_bytecode=True
assert os.geteuid()==0, 'requires robot sudo'
B=pathlib.Path('/var/backups/wuji-pc-initiated-20260908');ENV=pathlib.Path('/etc/default/wuji-hand2');STATE=pathlib.Path('/var/lib/deltacollect/config-guard')
spec=importlib.util.spec_from_file_location('guard','/usr/local/libexec/deltacollect-config-guard.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);guard=mod.Guard(STATE)
profile=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:5590/v1/hand-profile',timeout=3))
assert profile['profile']['robot_id']=='jetson-1794225001823', 'robot identity changed'
assert not subprocess.check_output(['ss','-tnH','state','established','( sport = :8765 or sport = :8767 )'],text=True).strip(), 'hand command session active'
listeners=subprocess.check_output(['ss','-ltnH'],text=True)
for port in (6013,6014,6015,6016):assert '127.0.0.1:'+str(port)+' ' in listeners, 'PC tunnel missing port '+str(port)
doc=guard.load()
for name,x in doc['files'].items():assert pathlib.Path(name).read_bytes()==base64.b64decode(x['data']), 'pre-existing guarded drift: '+name
old=ENV.read_bytes();assert old.count(b'DATA_COLLECTOR_HOST=10.1.10.239')==1,'unexpected sender config'
assert str(ENV) in doc['files'], 'env missing from guard'
assert not B.exists(), 'backup directory already exists; inspect previous transaction'
B.mkdir(mode=0o700)
(B/'wuji-hand2.env').write_bytes(old);(B/'wuji-hand2.env').chmod(0o600)
(B/'accepted.json').write_bytes((STATE/'accepted.json').read_bytes());(B/'accepted.json').chmod(0o600)
services=['wuji-hand2@left.service','wuji-hand2@right.service'];active={s:subprocess.run(['systemctl','is-active','--quiet',s]).returncode==0 for s in services}
(B/'state.json').write_text(json.dumps({'env_mode':ENV.stat().st_mode&0o777,'active':active,'original_sha256':hashlib.sha256(old).hexdigest()},indent=2))
new=old.replace(b'DATA_COLLECTOR_HOST=10.1.10.239',b'DATA_COLLECTOR_HOST=127.0.0.1')
try:
 mod.atomic_write(ENV,new,ENV.stat().st_mode&0o777);guard.accept([str(ENV)])
 for s,was_active in active.items():
  if was_active:subprocess.run(['systemctl','restart',s],check=True)
 assert ENV.read_bytes()==new, 'startup guard reverted changed env'
 for s,was_active in active.items():
  if was_active:subprocess.run(['systemctl','is-active','--quiet',s],check=True)
 (B/'applied.json').write_text(json.dumps({'telemetry_host':'127.0.0.1','services':active,'hardware_commands_sent':False},indent=2))
 print('APPLIED; backup='+str(B))
except BaseException:
 mod.atomic_write(ENV,old,ENV.stat().st_mode&0o777);guard.accept([str(ENV)])
 for s,was_active in active.items():
  if was_active:subprocess.run(['systemctl','restart',s],check=False)
 raise

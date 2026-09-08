import base64,importlib.util,json,os,pathlib,subprocess,sys
sys.dont_write_bytecode=True
assert os.geteuid()==0
B=pathlib.Path('/var/backups/wuji-pc-initiated-20260908');env=pathlib.Path('/etc/default/wuji-hand2')
assert not subprocess.check_output(['ss','-tnH','state','established','( sport = :8765 or sport = :8767 )'],text=True).strip(), 'hand client active; no rollback'
spec=importlib.util.spec_from_file_location('guard','/usr/local/libexec/deltacollect-config-guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);g=m.Guard('/var/lib/deltacollect/config-guard')
state=json.loads((B/'state.json').read_text());old=(B/'wuji-hand2.env').read_bytes()
assert env.read_bytes().replace(b'DATA_COLLECTOR_HOST=127.0.0.1',b'DATA_COLLECTOR_HOST=10.1.10.239')==old,'unrelated newer config exists; review rollback'
m.atomic_write(env,old,state['env_mode']);g.accept([str(env)])
for service,active in state['active'].items():
 subprocess.run(['systemctl','restart' if active else 'stop',service],check=True)
print('ROBOT_ENV_RESTORED; prior accepted entry restored without overwriting unrelated entries')

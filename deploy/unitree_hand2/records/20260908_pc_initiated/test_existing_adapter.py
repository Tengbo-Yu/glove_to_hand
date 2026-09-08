import subprocess,time,json,signal
service='deltacollect-hand-telemetry-tunnel.service'
assert subprocess.run(['systemctl','is-active','--quiet',service]).returncode!=0,'system tunnel active; no duplicate test'
assert not subprocess.check_output(['ssh','-o','BatchMode=yes','unitree@192.168.123.164','ss -ltnH | grep -E "127.0.0.1:601[3-6] " || true'],text=True).strip(),'existing robot reverse listeners'
p=subprocess.Popen(['/usr/bin/python3','/usr/local/lib/xrobotoolkit-local/robot_hand_adapter.py','--url','http://192.168.123.164:5590/v1/hand-profile','telemetry-tunnel','--host','192.168.123.164','--user','unitree'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
try:
 for i in range(15):
  if p.poll() is not None:raise RuntimeError(p.stderr.read().decode())
  s=subprocess.check_output(['ssh','-o','BatchMode=yes','unitree@192.168.123.164','ss -ltnH'],text=True)
  if all('127.0.0.1:'+str(port)+' ' in s for port in range(6013,6017)):
   print(json.dumps({'existing_adapter_passed':True,'robot_profile_and_ssh_key_verified':True,'remote_loopback_ports':[6013,6014,6015,6016],'commands_sent':False}));break
  time.sleep(.5)
 else:raise RuntimeError('existing adapter did not establish forwarding')
finally:
 if p.poll() is None:
  p.send_signal(signal.SIGINT)
  try:p.wait(timeout=12)
  except subprocess.TimeoutExpired:p.kill();p.wait()
 print(p.stderr.read().decode())

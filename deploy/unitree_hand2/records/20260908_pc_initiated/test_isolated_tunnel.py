# Runs on PC239 via SSH stdin. Synthetic data goes only to isolated port26014.
import subprocess,time,json,hashlib,zmq
port=26014;ctx=zmq.Context();pull=ctx.socket(zmq.PULL);pull.setsockopt(zmq.LINGER,0);pull.bind('tcp://127.0.0.1:'+str(port))
tunnel=None;publisher=None
header=b'{"sequence":17,"source_timestamp_ns":123456789,"source":"diagnostic_only","encoding":"msgpack"}'
payload=b'\x81\xa4test\xc3'
try:
 tunnel=subprocess.Popen(['ssh','-NT','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=2','-o','ServerAliveCountMax=2','-R',f'127.0.0.1:{port}:127.0.0.1:{port}','unitree@192.168.123.164'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 for i in range(20):
  if tunnel.poll() is not None:raise RuntimeError(tunnel.stderr.read().decode())
  check=subprocess.check_output(['ssh','-o','BatchMode=yes','unitree@192.168.123.164',f'ss -ltn sport = :{port}'],text=True)
  if '127.0.0.1:'+str(port) in check:break
  time.sleep(.2)
 else:raise RuntimeError('test reverse listener did not appear')
 code='import zmq,time\nc=zmq.Context();s=c.socket(zmq.PUSH);s.setsockopt(zmq.LINGER,1000);s.setsockopt(zmq.SNDTIMEO,3000);s.connect("tcp://127.0.0.1:'+str(port)+'");s.send_multipart('+repr([header,payload])+');time.sleep(.2);s.close();c.term()\n'
 publisher=subprocess.Popen(['ssh','-o','BatchMode=yes','unitree@192.168.123.164','python3 -'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 publisher.stdin.write(code.encode());publisher.stdin.close()
 assert pull.poll(5000), 'no tunneled multipart message'
 actual=pull.recv_multipart();assert actual==[header,payload], 'multipart bytes changed'
 assert publisher.wait(timeout=5)==0
 print(json.dumps({'passed':True,'path':'robot loopback26014 -> PC-initiated SSH to192.168.123.164 -> PC loopback26014','parts':len(actual),'sha256':hashlib.sha256(b''.join(actual)).hexdigest(),'production_ports_used':False,'hardware_commands_sent':False}))
finally:
 for p in [publisher,tunnel]:
  if p is not None and p.poll() is None:
   p.terminate()
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
 pull.close();ctx.term()

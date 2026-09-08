# Protocol handshake only: never send telemetry or control payloads.
import zmq,json,time
from zmq.utils.monitor import recv_monitor_message
ctx=zmq.Context();result=[]
for port in [6013,6014,6015,6016]:
 s=ctx.socket(zmq.PUSH);s.setsockopt(zmq.LINGER,0);monitor=s.get_monitor_socket();s.connect('tcp://127.0.0.1:'+str(port));events=[];deadline=time.monotonic()+4;ok=False
 while time.monotonic()<deadline:
  if monitor.poll(250):
   event=recv_monitor_message(monitor)['event'];events.append(int(event))
   if event==zmq.EVENT_HANDSHAKE_SUCCEEDED:ok=True;break
 s.disable_monitor();monitor.close();s.close();result.append({'port':port,'end_to_end_zmtp_handshake':ok,'events':events,'data_messages_sent':0})
ctx.term();print(json.dumps(result,indent=2));assert all(x['end_to_end_zmtp_handshake'] for x in result)

import argparse,getpass,pathlib,shlex,subprocess,sys
p=argparse.ArgumentParser();p.add_argument('host',choices=['pc','robot']);p.add_argument('script',type=pathlib.Path);a=p.parse_args()
def cmd(command):
 if a.host=='robot':command=shlex.join(['ssh','-o','BatchMode=yes','unitree@192.168.123.164',command])
 return ['ssh','-o','BatchMode=yes','delta@10.1.10.239',command]
dest='/tmp/wuji-pc-initiated-20260908-'+a.script.name
subprocess.run(cmd('umask 077; cat > '+shlex.quote(dest)),input=a.script.read_bytes(),check=True)
password=getpass.getpass(a.host+' sudo password: ')
result=subprocess.run(cmd("sudo -S -p '' python3 "+shlex.quote(dest)),input=(password+'\n').encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr);sys.exit(result.returncode)

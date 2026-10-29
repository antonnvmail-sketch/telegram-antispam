"""Private liveness metadata; never changes classification, feedback or training."""
import asyncio
try:
    import fcntl
except ImportError:
    fcntl = None
import json
import os
from pathlib import Path
import socket
import time

_instance=None

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp-'+str(os.getpid()))
    with temp.open('w',encoding='utf-8') as f:
        os.chmod(temp,0o600);json.dump(value,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)

def notify(message):
    address=os.environ.get('NOTIFY_SOCKET')
    if not address:return
    if address.startswith('@'):address='\0'+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        sock.connect(address);sock.sendall(message.encode())

def acquire_instance(directory=None):
    global _instance
    directory=Path(directory or os.environ.get('ANTISPAM_OPS_DIR','.operations'))
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    handle=(directory/'instance.lock').open('a+')
    try:
        if fcntl is not None:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        else:
            import msvcrt
            handle.seek(0);handle.write('0');handle.flush();handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        handle.close();raise RuntimeError('Another managed antispam instance already holds the lock') from None
    _instance=handle
    return directory

class Runtime:
    def __init__(self,directory):
        self.path=Path(directory)/'runtime.json';self.task=None
        self.state=dict(pid=os.getpid(),started_at=time.time(),loop_at=0,last_poll_success=None,
                        last_poll_error_at=None,last_poll_error_type=None,last_update_received=None,
                        last_update_completed=None,updates_received=0,updates_completed=0,handlers_failed=0,inflight=0)
    def flush(self):
        self.state['loop_at']=time.time();atomic_json(self.path,self.state)
    async def transport(self,make_request,bot,method):
        polling=getattr(method,'__api_method__','')=='getUpdates'
        try:result=await make_request(bot,method)
        except Exception as exc:
            if polling:
                self.state.update(last_poll_error_at=time.time(),last_poll_error_type=type(exc).__name__)
            raise
        if polling:self.state['last_poll_success']=time.time()
        return result
    async def updates(self,handler,event,data):
        self.state['last_update_received']=time.time();self.state['updates_received']+=1;self.state['inflight']+=1
        try:return await handler(event,data)
        except Exception:
            self.state['handlers_failed']+=1;raise
        finally:
            self.state['inflight']-=1;self.state['updates_completed']+=1;self.state['last_update_completed']=time.time()
    async def heartbeat(self):
        while True:
            self.flush();notify('WATCHDOG=1')
            await asyncio.sleep(10)
    async def start(self,**kwargs):
        self.flush();self.task=asyncio.create_task(self.heartbeat())
        notify('READY=1\nSTATUS=Polling startup; heartbeat active')
    async def stop(self,**kwargs):
        notify('STOPPING=1')
        if self.task:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass
        self.state['stopped_at']=time.time();self.flush()

def install_runtime(bot,dp,directory):
    runtime=Runtime(directory)
    bot.session.middleware(runtime.transport)
    dp.update.outer_middleware(runtime.updates)
    dp.startup.register(runtime.start);dp.shutdown.register(runtime.stop)
    return runtime

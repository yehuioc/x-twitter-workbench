"""配对传输；不包含平台选择器、内容模型或任意脚本 RPC。"""
import hashlib
import math
import re
import secrets
import threading
import time
import uuid
from .store import Problem

ACTIONS={'identity','snapshot','search','open_post','read_post','read_thread','scroll','bookmarks'}
class Bridge:
    def __init__(self, store):
        self.store=store; self.cv=threading.Condition(); self.clients={}; self.commands={}; self.pairing=None
        self.stop_at=time.time()  # restart never revives an old grant
    def code(self):
        with self.cv: self.pairing=(secrets.token_hex(5).upper(),time.monotonic()+600)
        return {'code':self.pairing[0],'expires_in':600}
    def pair(self, origin, code):
        if not re.fullmatch(r'chrome-extension://[a-p]{32}', origin): raise Problem('仅允许扩展配对')
        with self.cv:
            if not self.pairing or time.monotonic()>self.pairing[1] or not secrets.compare_digest(str(code),self.pairing[0]): raise Problem('配对码无效或过期')
            self.pairing=None; token=secrets.token_urlsafe(32)
            with self.store.db() as db: db.execute('INSERT INTO keys VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),origin))
        return {'token':token}
    def auth(self, origin, token):
        key=hashlib.sha256(token.encode()).hexdigest()
        with self.store.db() as db: row=db.execute('SELECT origin FROM keys WHERE hash=?',(key,)).fetchone()
        if not row or row[0]!=origin: raise Problem('扩展未配对或凭据已撤销')
        return key
    def status(self):
        with self.cv:
            return [{k:v for k,v in c.items() if k not in ('seen','key','client_id','last_grant')} | {'online':time.monotonic()-c['seen']<12,'control':c['control'] and time.monotonic()-c['seen']<12} for c in self.clients.values()]
    def poll(self,key,body):
        b=body.get('binding') or {}
        if not isinstance(b,dict) or not re.fullmatch(r'[a-f0-9-]{36}',str(b.get('session',''))) or type(b.get('tab_id')) is not int or b['tab_id']<=0: raise Problem('绑定无效')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',str(b.get('account',''))): raise Problem('无法核对 X 登录账号')
        rev=b.get('revision',0); grant=b.get('grant_at',0)
        if type(rev) is not int or rev<0 or type(grant) not in (int,float) or not math.isfinite(grant) or grant>time.time()+5: raise Problem('授权版本无效')
        instance=b.get('instance','legacy')
        if instance!='legacy' and not re.fullmatch(r'[a-f0-9-]{36}',str(instance)): raise Problem('扩展实例无效')
        client_id=key+':'+instance
        with self.cv:
            previous=self.clients.get(client_id)
            last_grant=max((previous or {}).get('last_grant',0),(previous or {}).get('grant_at',0))
            fresh_grant=b.get('control') is True and grant>last_grant
            if previous and b.get('control') is True and b['session']!=previous['session'] and not fresh_grant:
                return {'stop':True}
            if previous and rev<previous['revision'] and not fresh_grant: return {'stale':True}
            stopped=grant<=self.stop_at
            c={k:b.get(k) for k in ('session','tab_id','account','url','revision','grant_at','version')}
            c.update(key=key,client_id=client_id,last_grant=max(last_grant,grant),seen=time.monotonic(),control=b.get('control') is True and not stopped,busy=body.get('busy') is True,message=str(body.get('message') or '')[:500])
            self.clients[client_id]=c
            for cmd in self.commands.values():
                if cmd['client_id']==client_id and (not c['control'] or cmd['session']!=c['session']): cmd['error']='用户停止或重新绑定'
            self.cv.notify_all()
            flight=body.get('executing')
            if flight and (flight not in self.commands or self.commands[flight].get('error')): return {'cancel':flight}
            for cmd in self.commands.values():
                if cmd['client_id']==client_id and cmd['session']==c['session'] and c['control'] and not c['busy'] and not cmd.get('sent') and not cmd.get('error'):
                    cmd['sent']=True
                    return {k:cmd[k] for k in ('id','session','action','args','expires')}
            return {'stop':True} if stopped and b.get('control') else None
    def check_capture(self,key,binding):
        with self.cv:
            c=self.clients.get(key+':'+str(binding.get('instance','legacy')))
            if not c or c['session']!=binding.get('session') or c['account']!=binding.get('account') or time.monotonic()-c['seen']>12: raise Problem('绑定已变化，请重新同步页面状态')
    def stop(self):
        with self.cv:
            self.stop_at=time.time()
            for c in self.clients.values(): c['control']=False
            for cmd in self.commands.values(): cmd['error']='工作台已停止接管'
            self.cv.notify_all()
        return {'stopped':True}
    def revoke(self):
        self.stop()
        with self.cv:
            with self.store.db() as db: db.execute('DELETE FROM keys')
            self.clients.clear()
        return {'revoked':True}
    def result(self,key,body):
        with self.cv:
            cmd=self.commands.get(body.get('id'))
            if not cmd or cmd['key']!=key or cmd['session']!=body.get('session') or cmd.get('error') or time.time()>cmd['expires']: raise Problem('旧会话或迟到结果已拒绝')
            cmd.update(done=True,result=body.get('result'),error=body.get('error')); self.cv.notify_all()
        return {'accepted':True}
    def request(self,action,args=None,cancel=lambda:False,timeout=60):
        if action not in ACTIONS: raise Problem('未开放该站内动作')
        with self.cv:
            ready=[c for c in self.clients.values() if c['control'] and time.monotonic()-c['seen']<12]
            if len(ready)!=1: raise Problem('请在一个 X 扩展侧栏绑定页面并开启 AI 接管')
            c=ready[0]; cid=uuid.uuid4().hex
            cmd={'id':cid,'key':c['key'],'client_id':c['client_id'],'session':c['session'],'action':action,'args':args or {},'expires':time.time()+timeout}
            self.commands[cid]=cmd
            try:
                while not cmd.get('done') and not cmd.get('error'):
                    if cancel(): raise Problem('任务已取消')
                    if time.time()>=cmd['expires']: raise Problem('页面操作超时；未自动重放')
                    if time.monotonic()-self.clients.get(c['client_id'],c)['seen']>15: raise Problem('扩展连接中断')
                    self.cv.wait(.2)
                if cmd.get('error'): raise Problem(str(cmd['error']))
                return cmd.get('result')
            finally: self.commands.pop(cid,None)

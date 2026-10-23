import io
import json
import re
import secrets
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from .store import Store, Problem, ROOT, HOME, dumps
from .engine import Engine

def extension_archive():
    output=io.BytesIO()
    files=('manifest.json','background.js','buttons.js','site.js','panel.html','panel.js','panel.css')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in files: archive.write(ROOT/'extension'/name,name)
        for name in ('LICENSE','THIRD_PARTY_NOTICES.md'): archive.write(ROOT/name,name)
    return output.getvalue()

def export(engine,ids,kind):
    if not ids or len(ids)>500: raise Problem('请选择1–500条资料导出')
    posts=[engine.store.get(pid) for pid in dict.fromkeys(ids)]
    tasks=[t for t in engine.store.tasks() if any(r['id'] in ids for r in t['body'].get('sources',[]))]
    data={'format':'x-workbench-export-v1','posts':[], 'research':tasks}
    lines=['# X 资料导出','', '原文、个人笔记与模型结果分别保留；覆盖说明不表示全站或完整会话。','']
    files={}
    for p in posts:
        versions=[engine.store.get(p['id'],v['revision'])['source']|{'revision':v['revision']} for v in p['versions']]
        data['posts'].append(p|{'snapshots':versions})
        for snapshot in versions:
            for media in snapshot['media']:
                if media.get('file'): files[media['file']]=engine.store.media_file(media)
        s=p['source']; lines += ['## '+p['id'],'',s['url'],'','### 原文','',s['text'],'','### 读取覆盖','',dumps(s['coverage']),'','### 我的笔记','',p['note'],'','标签：'+', '.join(p['tags']),'','### 关系','',dumps(s['relations']),'']
        for m in s['media']:
            if m.get('file'):
                lines+=['![来源图片]('+m['file']+')','']
    for t in tasks:
        if t['body'].get('result'):
            lines+=['## 模型研究：'+t['body']['topic'],'','以下为模型生成，引用版本见 records.json。','',t['body']['result']['summary'],'','```json',dumps(t['body']['result']),'```','']
    if kind=='json': return dumps(data).encode(),'application/json; charset=utf-8','x-records.json'
    if kind!='md': raise Problem('未知导出格式')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('library.md','\n'.join(lines)); archive.writestr('records.json',dumps(data))
        for name,path in files.items(): archive.write(path,name)
    return output.getvalue(),'application/zip','x-markdown-and-media.zip'

def make_server(store=None,port=8915):
    engine=Engine(store or Store()); csrf=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def host(self):
            if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}: raise Problem('未知 Host')
        def local(self):
            self.host(); origin=self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}: raise Problem('拒绝跨站请求')
        def origin(self):
            self.host(); origin=self.headers.get('Origin','')
            if not re.fullmatch(r'chrome-extension://[a-p]{32}',origin): raise Problem('仅允许已配对扩展来源')
            return origin
        def send(self,value,code=200,mime='application/json; charset=utf-8',headers=None):
            if not isinstance(value,bytes): value=dumps(value).encode()
            self.send_response(code)
            for k,v in {'Content-Type':mime,'Content-Length':str(len(value)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",**(headers or {})}.items(): self.send_header(k,v)
            self.end_headers(); self.wfile.write(value)
        def body(self):
            if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise Problem('仅接受 JSON')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=36_000_000: raise Problem('请求为空或过大')
            value=json.loads(self.rfile.read(length))
            if not isinstance(value,dict): raise Problem('请求须为对象')
            return value
        def do_OPTIONS(self):
            try:
                origin=self.origin()
                if not self.path.startswith('/bridge/'): raise Problem('不开放跨站接口')
                self.send({},headers={'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type, Authorization','Vary':'Origin'})
            except (Problem,ValueError): self.send({'error':'跨站接口拒绝'},403)
        def do_GET(self):
            try:
                self.local(); url=urlsplit(self.path); q=parse_qs(url.query); path=unquote(url.path)
                if path=='/api/state':
                    state=engine.status(q.get('since',[None])[0])|{'csrf':csrf}
                    if not state['unchanged']:
                        state['installation']={'extension_dir':str(ROOT/'extension'),'mcp_command':'python -X utf8 -B "'+ROOT.as_posix()+'/run_x.py" mcp'}
                    return self.send(state)
                if path=='/api/library': return self.send(engine.store.search(q.get('q',[''])[0],q.get('tag',[''])[0],q.get('offset',[0])[0],brief=True))
                if path=='/api/post': return self.send(engine.store.get(q['id'][0],q.get('revision',[None])[0]))
                if path=='/api/task': return self.send(engine.store.task(q['id'][0]))
                if path=='/api/packet': return self.send(engine.packet(q['id'][0]))
                if path=='/api/export':
                    data,mime,name=export(engine,q.get('ids',[''])[0].split(','),q.get('format',['json'])[0])
                    return self.send(data,mime=mime,headers={'Content-Disposition':'attachment; filename="'+name+'"'})
                if path=='/extension.zip':
                    return self.send(extension_archive(),mime='application/zip',headers={'Content-Disposition':'attachment; filename="x-workbench-extension.zip"'})
                if path.startswith('/media/'):
                    name=path.rsplit('/',1)[1]
                    if not re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|gif|webp)',name): raise Problem('图片路径无效')
                    mime={'png':'image/png','jpg':'image/jpeg','webp':'image/webp','gif':'image/gif'}[name.split('.')[-1]]
                    return self.send(engine.store.media_file({'file':'media/'+name,'sha256':name.split('.')[0]}).read_bytes(),mime=mime)
                static={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/app.css':('app.css','text/css')}
                if path in static:
                    name,mime=static[path]; return self.send((ROOT/'x_workbench'/'static'/name).read_bytes(),mime=mime+'; charset=utf-8')
                self.send({'error':'入口不存在'},404)
            except (Problem,ValueError,KeyError,TypeError,IndexError,OSError) as e: self.send({'error':str(e)},400)
        def do_POST(self):
            extra={}
            try:
                path=urlsplit(self.path).path
                if path.startswith('/bridge/'):
                    origin=self.origin(); extra={'Access-Control-Allow-Origin':origin,'Vary':'Origin'}; body=self.body()
                    if path=='/bridge/pair': result=engine.bridge.pair(origin,body.get('code'))
                    else:
                        token=self.headers.get('Authorization','').removeprefix('Bearer '); key=engine.bridge.auth(origin,token)
                        if path=='/bridge/poll': result=engine.bridge.poll(key,body)
                        elif path=='/bridge/result': result=engine.bridge.result(key,body)
                        elif path=='/bridge/capture':
                            engine.bridge.check_capture(key,body.get('binding',{})); result=engine.save(body)
                        else: raise Problem('扩展接口不存在')
                else:
                    self.local(); body=self.body()
                    if path=='/api/v1':
                        file=engine.store.home/'api-token.txt'
                        if not file.is_file() or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+file.read_text(encoding='utf-8')): raise Problem('AI 接口凭据无效')
                        result=engine.dispatch(body['action'],body.get('args',{}),external=True)
                    elif path=='/api/action':
                        if not secrets.compare_digest(self.headers.get('X-CSRF',''),csrf): raise Problem('页面令牌无效，请刷新工作台')
                        result=engine.dispatch(body['action'],body.get('args',{}))
                    else: raise Problem('接口不存在')
                self.send({'result':result},headers=extra)
            except (Problem,ValueError,KeyError,TypeError,IndexError,OSError) as e: self.send({'error':str(e)},400,headers=extra)
            except Exception:
                import traceback
                traceback.print_exc(); self.send({'error':'内部操作失败，请查看服务日志'},500,headers=extra)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True; server.engine=engine
    return server

def serve(port=8915):
    server=make_server(port=port)
    print(f'X 资料工作台 http://127.0.0.1:{server.server_port}/',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.engine.close(); server.server_close()

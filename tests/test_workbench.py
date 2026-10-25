"""All posts, accounts, text, credentials and media in these tests are synthetic."""
import base64
import copy
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import zipfile
from pathlib import Path
from x_workbench.store import Store, ROOT, Problem
from x_workbench.engine import Engine
from x_workbench.server import make_server, export, extension_archive
from x_workbench import mcp

PID='9000000000000000000000001'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
def source(pid=PID,text='完整原文：独立开发者验证付费需求。',complete=True):
    return {'id':pid,'text':text,'author':{'id':'9000000000000000000000100','handle':'reader','name':'合成测试用户'},'published_at':'2030-01-01T01:00:00Z','coverage':{'text_complete':complete,'thread':'not_collected'},'relations':[],'media':[]}

class Fixture(unittest.TestCase):
    def setUp(self):
        tmp=ROOT/'tests'/'.tmp';tmp.mkdir(parents=True,exist_ok=True)
        self.directory=Path(tempfile.mkdtemp(dir=tmp));self.store=Store(self.directory);self.engine=Engine(self.store)
    def tearDown(self): self.engine.close();shutil.rmtree(self.directory)
    def wait_task(self,tid,state):
        end=time.monotonic()+5
        while time.monotonic()<end:
            t=self.store.task(tid)
            if t['state']==state and tid not in self.engine.running:return t
            time.sleep(.02)
        self.fail(str(self.store.task(tid)))
    def test_duplicate_version_note_and_partial_capture(self):
        first=self.store.capture(source());second=self.store.capture(source()|{'observed_at':'2030-01-01'})
        self.assertEqual(first['revision'],second['revision'])
        self.store.annotate(PID,'自己的判断',['研究','研究','中文'])
        self.store.capture(source(text='列表摘要',complete=False))
        p=self.store.get(PID);self.assertIn('完整原文',p['source']['text']);self.assertEqual(len(p['versions']),2);self.assertEqual(p['tags'],['研究','中文'])
        self.store.capture(source(text='作者编辑后的完整原文'))
        self.assertEqual(self.store.get(PID,first['revision'])['source']['text'],source()['text'])
        self.assertEqual(Store(self.directory).get(PID)['note'],'自己的判断')
    def test_fulltext_chinese_punctuation_and_tag(self):
        self.store.capture(source());self.store.annotate(PID,'literal % _ test',['验证'])
        self.assertEqual(self.store.search('付费 需求',tag='验证')['total'],1)
        self.assertEqual(self.store.search('% _')['total'],1)
        self.assertEqual(self.store.search("' OR 1=1 --")['total'],0)
        self.assertEqual(self.store.search('付费',tag='不存在')['total'],0)
    def test_media_export_and_invalid_inputs(self):
        s=source();s['media']=[{'url':'https://pbs.twimg.com/media/a.png','data':base64.b64encode(PNG).decode()}]
        self.store.capture(s)
        data,_,_=export(self.engine,[PID],'md')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('完整原文',z.read('library.md').decode());self.assertEqual(sum(x.startswith('media/') for x in z.namelist()),1)
            self.assertEqual(json.loads(z.read('records.json'))['posts'][0]['id'],PID)
        for invalid in (9000000000000000000000001,'../etc','NaN'):
            with self.assertRaises(Problem):self.store.capture(source(pid=invalid))
        for url in ('https://evil.invalid/a','http://pbs.twimg.com/a','https://pbs.twimg.com.evil.invalid/a'):
            with self.assertRaises(Problem):self.store.capture(s|{'media':[{'url':url,'data':base64.b64encode(PNG).decode()}]})
        with self.assertRaises(Problem):self.store.capture(s|{'media':[{'url':'https://pbs.twimg.com/a','data':base64.b64encode(b'<script>').decode()}]})
    def test_task_packet_quote_validation_and_cancel(self):
        ref=self.store.capture(source());t=self.engine.create({'topic':'研究需求','source_ids':[PID]});self.engine.run(t['id']);self.wait_task(t['id'],'awaiting_model')
        packet=self.engine.packet(t['id']);self.assertEqual(packet['sources'][0]['revision'],ref['revision'])
        artifact={'summary':'模型归纳','findings':[{'claim':'需要验证','post_id':PID,'revision':ref['revision'],'quote':'验证付费需求'}],'translations':[],'uncertainties':['尚无效果证据']}
        bad=copy.deepcopy(artifact);bad['findings'][0]['quote']='不存在的引文'
        with self.assertRaises(Problem):self.engine.complete(t['id'],bad)
        self.engine.complete(t['id'],artifact);self.assertEqual(self.store.task(t['id'])['state'],'completed')
        self.assertEqual(self.store.search('模型归纳')['total'],1)
        another=self.engine.create({'topic':'另一次','source_ids':[PID]});self.engine.run(another['id']);self.wait_task(another['id'],'awaiting_model');self.engine.dispatch('cancel',{'id':another['id']})
        with self.assertRaises(Problem):self.engine.complete(another['id'],artifact)
        self.assertEqual(self.store.get(PID)['source']['text'],source()['text'])
    def test_online_research_through_real_bridge_command_loop(self):
        b=self.engine.bridge;origin='chrome-extension://'+'a'*32;key=b.auth(origin,b.pair(origin,b.code()['code'])['token'])
        binding={'session':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','tab_id':7,'account':'reader','revision':1,'grant_at':time.time()+.01,'control':True}
        b.poll(key,{'binding':binding});stop=threading.Event();actions=[]
        def extension():
            while not stop.is_set():
                command=b.poll(key,{'binding':binding})
                if command and command.get('id'):
                    a=command['action'];actions.append(a)
                    result={'cards':[{'id':PID}],'url':'https://x.com/search?q=sample'} if a=='search' else {'opened':True} if a=='open_post' else {'posts':[source()]}
                    b.result(key,{'id':command['id'],'session':binding['session'],'result':result})
                time.sleep(.01)
        thread=threading.Thread(target=extension);thread.start()
        try:
            t=self.engine.create({'topic':'sample','max_sources':1});self.engine.run(t['id']);self.wait_task(t['id'],'awaiting_model')
            self.assertEqual(actions,['search','read_post']);self.assertEqual(self.engine.packet(t['id'])['sources'][0]['source']['text'],source()['text'])
        finally:stop.set();thread.join()
    def test_collection_resume_keeps_candidates_and_reads_only_unfinished(self):
        ids=['101','102','103'];calls=[];fail=True
        def browser(operation,args,cancel):
            nonlocal fail
            calls.append((operation,args.get('id')))
            if operation=='search':return {'cards':[{'id':i} for i in ids]}
            if operation=='read_post':
                if args['id']=='102' and fail:fail=False;raise Problem('simulated connection interruption')
                return self.engine.save({'posts':[source(args['id'])]})
            self.fail('unexpected page operation: '+operation)
        self.engine.browser=browser
        t=self.engine.create({'topic':'在线断点','source_mode':'online','max_sources':3,'max_scrolls':0})
        self.engine.run(t['id']);failed=self.wait_task(t['id'],'failed')
        self.assertEqual([r['id'] for r in failed['body']['sources']],['101'])
        self.assertEqual([c['state'] for c in failed['body']['collection']['candidates']],['done','failed','pending'])
        other=Engine(Store(self.directory));self.engine.close();self.engine=other;other.browser=browser
        other.run(t['id']);done=self.wait_task(t['id'],'awaiting_model')
        self.assertEqual([r['id'] for r in done['body']['sources']],ids)
        self.assertEqual(calls,[('search',None),('read_post','101'),('read_post','102'),('read_post','102'),('read_post','103')])
        self.assertTrue(done['body']['collection']['complete'])
        other.run(t['id']);self.wait_task(t['id'],'awaiting_model');self.assertEqual(len(calls),5)
    def test_local_reuse_online_refresh_and_hybrid_supplement(self):
        first=self.store.capture(source('101'));self.store.capture(source('102',complete=False));calls=[]
        def browser(operation,args,cancel):
            calls.append(operation)
            if operation=='search':return {'cards':[{'id':'103'}]}
            return self.engine.save({'posts':[source('103')]})
        self.engine.browser=browser
        local=self.engine.create({'topic':'验证付费需求','source_mode':'local'});self.engine.run(local['id']);self.wait_task(local['id'],'awaiting_model')
        self.assertEqual(calls,[]);ref=self.engine.packet(local['id'])['sources'][0];self.assertEqual(ref['revision'],first['revision'])
        hybrid=self.engine.create({'topic':'验证付费需求','source_mode':'hybrid','max_sources':2,'max_scrolls':0});self.engine.run(hybrid['id']);task=self.wait_task(hybrid['id'],'awaiting_model')
        self.assertEqual([r['origin'] for r in task['body']['sources']],['local','online']);self.assertEqual(calls,['search','read_post'])
        online=self.engine.create({'topic':'验证付费需求','source_mode':'online','max_sources':1});self.engine.run(online['id']);self.wait_task(online['id'],'awaiting_model');self.assertEqual(calls,['search','read_post','search','read_post'])
        self.assertEqual(self.store.local_sources('',[]),[])
        self.assertEqual(self.store.local_sources('from:reader to:other',['from:reader to:other']),[])
    def test_search_index_migration_short_terms_and_current_content(self):
        ref=self.store.capture(source());self.store.annotate(PID,'中文二字词：付费；literal % _ test',['验证'])
        t=self.engine.create({'topic':'已存研究','source_ids':[PID]});self.engine.run(t['id']);self.wait_task(t['id'],'awaiting_model')
        self.engine.complete(t['id'],{'summary':'独立的模型索引结果','findings':[],'translations':[],'uncertainties':[]})
        with self.store.db() as db:
            db.executescript('DROP TABLE search_fts; DROP TABLE search_docs; DROP TABLE task_sources; PRAGMA user_version=0;')
        migrated=Store(self.directory)
        for query in ('付费','验证付费需求','% _','独立的模型索引结果'):
            self.assertEqual(migrated.search(query)['total'],1,query)
        self.assertEqual(migrated.get(PID)['note'],'中文二字词：付费；literal % _ test')
        self.assertEqual(migrated.get(PID,ref['revision'])['source']['text'],source()['text'])
        self.assertEqual(migrated.search("\" OR 1=1 --")['total'],0)
        migrated.capture(source(text='已替换的原文新内容'));self.assertEqual(migrated.search('独立开发者')['total'],0)
        self.assertEqual(migrated.search('独立的模型索引结果')['total'],1)
        brief=migrated.search(brief=True)['items'][0];self.assertNotIn('versions',brief);self.assertNotIn('note',brief)
        self.assertIn('versions',migrated.search()['items'][0])
    def test_delta_status_and_summary_do_not_ship_result_or_events(self):
        self.store.capture(source());t=self.engine.create({'topic':'状态测试','source_ids':[PID]});self.engine.run(t['id']);self.wait_task(t['id'],'awaiting_model')
        full=self.engine.status();same=self.engine.status(full['stamp']);self.assertTrue(same['unchanged']);self.assertNotIn('tasks',same)
        self.assertNotIn('events',full['tasks'][0]);self.assertNotIn('result',full['tasks'][0]['body'])
        self.store.event(t['id'],'新事件');self.assertFalse(self.engine.status(full['stamp'])['unchanged'])
    def test_old_extension_rejected_before_combined_read_and_preview_validation(self):
        self.engine.bridge.status=lambda:[{'control':True,'version':'0.1.1'}]
        with self.assertRaisesRegex(Problem,'0.2.0'):self.engine.browser('read_post',{'id':PID})
        with self.assertRaises(Problem):self.engine.dispatch('local_sources',{'keywords':'not a list'},external=True)
    def test_model_api_shape_and_image_attachment_without_paid_call(self):
        import os
        raw=source();raw['media']=[{'url':'https://pbs.twimg.com/media/test.png','data':base64.b64encode(PNG).decode()}]
        ref=self.store.capture(raw);artifact={'summary':'本地模拟模型结果','findings':[],'translations':[],'uncertainties':['模拟接口']};received=[]
        class Model(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                data=json.dumps({'choices':[{'message':{'content':json.dumps(artifact)}}]}).encode();self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Model);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();old=os.environ.get('XWB_TEST_KEY');os.environ['XWB_TEST_KEY']='only-local-fixture'
        self.store.configure('provider',{'api_url':f'http://127.0.0.1:{server.server_port}/v1','key_env':'XWB_TEST_KEY','model':'fixture'})
        try:
            t=self.engine.create({'topic':'本地模型协议验收','source_ids':[PID],'driver':'api'});self.engine.run(t['id']);self.wait_task(t['id'],'completed')
            path,auth,body=received[0];self.assertEqual(path,'/v1/chat/completions');self.assertEqual(auth,'Bearer only-local-fixture');self.assertTrue(body['response_format']['json_schema']['strict']);self.assertEqual(len(body['messages'][0]['content']),2);self.assertEqual(self.store.task(t['id'])['body']['result'],artifact)
        finally:
            if old is None:os.environ.pop('XWB_TEST_KEY',None)
            else:os.environ['XWB_TEST_KEY']=old
            server.shutdown();server.server_close();thread.join()
    def test_interrupted_tasks_recover_without_running(self):
        t=self.engine.create({'topic':'不会重放'});self.store.update_task(t['id'],'running');other=Store(self.directory)
        self.assertEqual(other.task(t['id'])['state'],'interrupted')
    def test_bridge_pair_origin_replay_stop_restart(self):
        b=self.engine.bridge;origin='chrome-extension://'+'a'*32;code=b.code()['code'];token=b.pair(origin,code)['token'];key=b.auth(origin,token)
        with self.assertRaises(Problem):b.pair(origin,code)
        with self.assertRaises(Problem):b.auth('chrome-extension://'+'b'*32,token)
        binding={'session':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','tab_id':7,'account':'reader','revision':2,'grant_at':time.time()+.01,'control':True}
        b.poll(key,{'binding':binding});got={}
        def request():
            try:got['result']=b.request('snapshot',timeout=2)
            except Problem as e:got['error']=str(e)
        thread=threading.Thread(target=request);thread.start()
        end=time.monotonic()+2;cmd=None
        while time.monotonic()<end:
            cmd=b.poll(key,{'binding':binding})
            if cmd and cmd.get('id'):break
            time.sleep(.01)
        self.assertIsNotNone(cmd);b.result(key,{'id':cmd['id'],'session':binding['session'],'result':{'cards':[]}});thread.join();self.assertEqual(got['result'],{'cards':[]})
        with self.assertRaises(Problem):b.result(key,{'id':cmd['id'],'session':binding['session'],'result':{}})
        time.sleep(.02);b.stop();self.assertTrue(b.poll(key,{'binding':binding})['stop'])
        with self.assertRaises(Problem):b.request('snapshot',timeout=.1)
        with self.assertRaises(Problem):b.request('publish')
    def test_extension_reload_revision_reset_and_old_grant_rejection(self):
        b=self.engine.bridge;origin='chrome-extension://'+'a'*32;key=b.auth(origin,b.pair(origin,b.code()['code'])['token'])
        old={'session':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','tab_id':7,'account':'reader','revision':8,'grant_at':time.time()+.01,'control':True}
        b.poll(key,{'binding':old})
        new=old|{'session':'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','revision':1,'grant_at':old['grant_at']+.01}
        self.assertIsNone(b.poll(key,{'binding':new}));self.assertTrue(b.status()[0]['control'])
        self.assertTrue(b.poll(key,{'binding':old})['stop'])
        stopped=new|{'session':'cccccccc-cccc-cccc-cccc-cccccccccccc','control':False,'grant_at':0,'revision':2}
        b.poll(key,{'binding':stopped});self.assertTrue(b.poll(key,{'binding':new})['stop'])
        manual=stopped|{'instance':'dddddddd-dddd-dddd-dddd-dddddddddddd','revision':1}
        self.assertIsNone(b.poll(key,{'binding':manual}));b.check_capture(key,manual)
    def test_http_real_routes_csrf_origins_auth_and_persistence(self):
        server=make_server(self.store,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,body=None,headers=None):
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
            with opener.open(req,timeout=3) as r:return r.status,r.headers,r.read()
        try:
            state=json.loads(request('/api/state')[2]);csrf=state['csrf'];self.assertIn('资料工作台',request('/')[2].decode())
            self.assertEqual(state['installation']['extension_dir'],str(ROOT/'extension'))
            self.assertIn(ROOT.as_posix(),state['installation']['mcp_command'])
            with self.assertRaises(urllib.error.HTTPError):request('/api/action',{'action':'pair'})
            with self.assertRaises(urllib.error.HTTPError):request('/api/state',headers={'Origin':'https://evil.invalid'})
            with self.assertRaises(urllib.error.HTTPError):request('/api/state',headers={'Host':'evil.invalid'})
            token=json.loads(request('/api/action',{'action':'token'}, {'X-CSRF':csrf})[2])['result']['token']
            with self.assertRaises(urllib.error.HTTPError):request('/api/v1',{'action':'status'})
            with self.assertRaises(urllib.error.HTTPError):request('/api/v1',{'action':'pair'}, {'Authorization':'Bearer '+token})
            self.assertEqual(request('/api/v1',{'action':'status'}, {'Authorization':'Bearer '+token})[0],200)
            self.store.capture(source());self.assertEqual(json.loads(request('/api/library?q=付费'.replace('付费','%E4%BB%98%E8%B4%B9'))[2])['total'],1)
            self.assertEqual(json.loads(request('/api/export?ids='+PID+'&format=json')[2])['posts'][0]['id'],PID)
            with self.assertRaises(urllib.error.HTTPError):request('/media/../api-token.txt')
            original=mcp.HOME;mcp.HOME=self.directory
            try:
                result=mcp.response({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'x_get','arguments':{'id':PID}}},server.server_port)
                self.assertFalse(result['result']['isError']);self.assertIn(PID,result['result']['content'][0]['text'])
            finally:mcp.HOME=original
        finally:server.engine.close();server.shutdown();server.server_close();thread.join()
    def test_extension_package_has_only_code_and_licenses(self):
        with zipfile.ZipFile(io.BytesIO(extension_archive())) as archive:
            expected={'manifest.json','background.js','buttons.js','site.js','panel.html','panel.js','panel.css','LICENSE','THIRD_PARTY_NOTICES.md'}
            self.assertEqual(set(archive.namelist()),expected)
            self.assertEqual(json.loads(archive.read('manifest.json'))['version'],'0.3.0')
            self.assertIn('Copyright (c) 2026 angiin',archive.read('THIRD_PARTY_NOTICES.md').decode())

if __name__=='__main__':unittest.main()

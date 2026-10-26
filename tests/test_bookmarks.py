"""Synthetic bookmark pages and archives; no account or network access."""
import base64
import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from x_workbench.store import Store, ROOT, Problem
from x_workbench.engine import Engine
from x_workbench.server import make_server
from x_workbench import bookmarks, mcp

PID='9000000000000000000000001'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
def post(pid=PID,complete=True):return {'id':pid,'text':'合成的书签正文 '+pid,'author':{'handle':'writer'},'coverage':{'text_complete':complete},'relations':[],'media':[]}
def view(ids,complete=True,account='reader',empty=False):return {'root_ids':ids,'posts':[post(pid,complete) for pid in ids],'account':account,'url':'https://x.com/i/bookmarks','empty':empty}

class BookmarkTests(unittest.TestCase):
    def setUp(self):
        directory=ROOT/'tests'/'.tmp';directory.mkdir(exist_ok=True)
        self.home=Path(tempfile.mkdtemp(dir=directory));self.store=Store(self.home);self.engine=Engine(self.store)
        self.engine.bridge.status=lambda:[{'online':True,'control':True,'account':'reader','version':'0.3.0'}]
    def tearDown(self):self.engine.close();shutil.rmtree(self.home)
    def test_history_route_needs_explicit_selected_bookmark_evidence(self):
        page=view(['101'])|{'url':'https://x.com/i/history'}
        with self.assertRaises(Problem):bookmarks.save_view(self.engine,page,'reader')
        page.update(collection='bookmarks',route_evidence='selected_bookmarks_tab')
        self.assertEqual(bookmarks.save_view(self.engine,page,'reader')['bookmark_ids'],['101'])
        for patch in ({'route_evidence':'viewed_history_tab'},{'url':'https://x.com/i/history?folder_id=1'},{'url':'https://x.com/i/history/folders/1'}):
            with self.assertRaises(Problem):bookmarks.save_view(self.engine,page|patch,'reader')
    def run_sync(self,pages,**args):
        calls=[];index=0
        def browser(operation,a,cancel):
            nonlocal index
            calls.append((operation,a))
            if operation=='read_post':return self.engine.save({'posts':[post(a['id'])]})
            if a['mode']=='open':index=0
            elif a['mode']=='scroll':index+=1
            return pages[min(index,len(pages)-1)]
        self.engine.browser=browser
        t=bookmarks.create_sync(self.store,{'max_scrolls':4,'max_items':100,'max_seconds':30}|args)
        self.store.update_task(t['id'],'running')
        bookmarks.sync(self.engine,t['id'],time.monotonic()+10)
        return self.store.task(t['id']),calls
    def test_scoped_membership_notes_dedupe_and_no_false_removal(self):
        self.store.capture(post());self.store.annotate(PID,'用户笔记',['有用'])
        self.store.mark_bookmarks('reader',[PID]);self.store.mark_bookmarks('reader',[PID]);self.store.mark_bookmarks('other',[PID])
        self.assertEqual(self.store.bookmark_status()['total'],1)
        self.assertEqual(self.store.search(collection='bookmarks',owner='reader')['total'],1)
        self.assertEqual(self.store.search(collection='bookmarks',owner='nobody')['total'],0)
        t,_=self.run_sync([view([],empty=True)])
        self.assertEqual(t['body']['bookmark_result']['reason'],'empty_observed')
        self.assertEqual(self.store.bookmark_ids('reader'),{PID});self.assertEqual(self.store.get(PID)['note'],'用户笔记')
    def test_cursor_never_skips_items_truncated_by_budget(self):
        ids=['101','102','103'];t,_=self.run_sync([view(ids)],mode='backfill',max_items=1)
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'101')
        self.assertEqual(self.store.bookmark_ids('reader'),{'101'})
        t,_=self.run_sync([view(ids)],mode='backfill',max_items=1)
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'102')
        self.assertEqual(self.store.bookmark_ids('reader'),{'101','102'})
    def test_recent_does_not_change_backfill_and_stall_is_partial(self):
        self.store.configure('bookmark_backfill:reader',{'anchor':'999'})
        t,calls=self.run_sync([view(['101'])],mode='recent')
        self.assertEqual(t['body']['bookmark_result']['reason'],'stalled')
        self.assertIn('不代表全部历史',t['body']['bookmark_result']['scope'])
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'999')
        self.assertEqual(sum(c[0]=='read_post' for c in calls),0)
    def test_missing_anchor_and_account_drift_do_not_advance_history(self):
        self.store.configure('bookmark_backfill:reader',{'anchor':'999'})
        t,_=self.run_sync([view(['101'])],mode='backfill')
        self.assertEqual(t['body']['bookmark_result']['reason'],'anchor_not_found')
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'999')
        with self.assertRaisesRegex(Problem,'账号'):self.run_sync([view(['102'],account='other')])
        self.assertNotIn('102',self.store.bookmark_ids('reader'))
    def test_deleted_anchor_can_be_reset_without_deleting_bookmarks(self):
        self.store.configure('bookmark_backfill:reader',{'anchor':'999'})
        self.store.capture(post('999'));self.store.mark_bookmarks('reader',['999'])
        t,_=self.run_sync([view(['101'])],mode='backfill',reset_position=True,max_items=1,max_scrolls=1)
        self.assertNotEqual(t['body']['bookmark_result']['reason'],'anchor_not_found')
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'101')
        self.assertIn('999',self.store.bookmark_ids('reader'))
    def test_history_seeking_does_not_spend_new_page_budget(self):
        for pid in ['101','102']:
            self.store.capture(post(pid));self.store.mark_bookmarks('reader',[pid])
        self.store.configure('bookmark_backfill:reader',{'anchor':'102'})
        t,calls=self.run_sync([view(['100']),view(['101']),view(['102']),view(['103']),view(['104'])],mode='backfill',max_scrolls=1)
        result=t['body']['bookmark_result']
        self.assertEqual(result['seek_scrolls'],2);self.assertEqual(result['continued_scrolls'],1)
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'103')
        self.assertEqual(self.store.bookmark_ids('reader'),{'101','102','103'})
        self.assertNotIn('100',self.store.bookmark_ids('reader'))
    def test_history_advances_across_rounds_and_store_restart(self):
        pages=[view([str(pid)]) for pid in range(101,108)]
        self.run_sync(pages,mode='backfill',max_scrolls=1)
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'102')
        self.engine.close();self.store=Store(self.home);self.engine=Engine(self.store)
        self.engine.bridge.status=lambda:[{'online':True,'control':True,'account':'reader','version':'0.3.0'}]
        for expected in ('103','104','105'):
            self.run_sync(pages,mode='backfill',max_scrolls=1)
            self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],expected)
        self.assertEqual(self.store.bookmark_ids('reader'),{str(pid) for pid in range(101,106)})
    def test_warm_history_page_continues_without_resetting_to_top(self):
        self.engine.bridge.status=lambda:[{'online':True,'control':True,'account':'reader','version':'0.3.0','url':'https://x.com/i/bookmarks'}]
        self.store.configure('bookmark_backfill:reader',{'anchor':'102'})
        t,calls=self.run_sync([view(['102','103']),view(['104'])],mode='backfill',max_scrolls=1)
        self.assertFalse(calls[0][1]['reset'])
        self.assertEqual(t['body']['bookmark_result']['seek_scrolls'],0)
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'104')
        self.assertNotIn('102',self.store.bookmark_ids('reader'))
    def test_warm_page_without_anchor_reopens_top_before_seeking(self):
        self.engine.bridge.status=lambda:[{'online':True,'control':True,'account':'reader','version':'0.3.0','url':'https://x.com/i/bookmarks'}]
        self.store.configure('bookmark_backfill:reader',{'anchor':'102'});calls=[];index=0
        def browser(operation,args,cancel):
            nonlocal index
            calls.append(args)
            if args['mode']=='open' and not args['reset']:return view(['777'])
            if args['mode']=='open':index=101
            else:index+=1
            return view([str(index)])
        self.engine.browser=browser;t=bookmarks.create_sync(self.store,{'mode':'backfill','max_scrolls':1});self.store.update_task(t['id'],'running')
        bookmarks.sync(self.engine,t['id'],time.monotonic()+5)
        self.assertEqual([a['reset'] for a in calls if a['mode']=='open'],[False,True])
        self.assertEqual(self.store.bookmark_ids('reader'),{'103'})
    def test_tall_card_scroll_progress_does_not_look_like_end_of_list(self):
        pages=[view(['101'])|{'position':{'y':y}} for y in (0,700,1400,2100)]+[view(['102'])|{'position':{'y':2800}}]
        t,_=self.run_sync(pages,max_scrolls=4)
        self.assertEqual(self.store.bookmark_ids('reader'),{'101','102'})
        self.assertEqual(t['body']['bookmark_result']['reason'],'scroll_budget')
    def test_seeking_deadline_preserves_history_and_does_not_capture_head(self):
        self.store.configure('bookmark_backfill:reader',{'anchor':'999'})
        def browser(*args):time.sleep(.02);return view(['101'])
        self.engine.browser=browser;t=bookmarks.create_sync(self.store,{'mode':'backfill'});self.store.update_task(t['id'],'running')
        bookmarks.sync(self.engine,t['id'],time.monotonic()+.001)
        self.assertEqual(self.store.task(t['id'])['body']['bookmark_result']['reason'],'seek_time_budget')
        self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'999')
        self.assertFalse(self.store.bookmark_ids('reader'))
    def test_explicitly_resuming_cancelled_bookmark_batch_finishes_pending_details(self):
        saved=self.store.capture(post('101',complete=False));self.store.mark_bookmarks('reader',['101'])
        t=bookmarks.create_sync(self.store,{'mode':'backfill','max_scrolls':1})
        self.store.update_task(t['id'],'cancelled',{'owner':'reader','sources':[{'id':'101','revision':saved['revision']}],'bookmark_progress':{'anchor':'101','pages':1,'queue':[{'id':'101','state':'pending'}]}})
        calls=[]
        def browser(operation,args,cancel):
            calls.append(operation)
            if operation=='read_post':return self.engine.save({'posts':[post('101')]})
            return view(['101'])
        self.engine.browser=browser;self.engine.run(t['id']);end=time.monotonic()+5
        while t['id'] in self.engine.running and time.monotonic()<end:time.sleep(.01)
        current=self.store.task(t['id'])
        self.assertEqual(current['state'],'completed',current['body'].get('error'))
        self.assertEqual(current['body']['bookmark_progress']['queue'][0]['state'],'done')
        self.assertEqual(calls.count('read_post'),1)
    def test_partial_details_checkpoint_and_retry_without_changing_account(self):
        t,calls=self.run_sync([view(['101'],complete=False)],max_scrolls=1)
        self.assertEqual(t['body']['bookmark_progress']['queue'][0]['state'],'done')
        self.assertEqual(sum(c[0]=='read_post' for c in calls),1)
        self.assertTrue(self.store.get('101')['source']['coverage']['text_complete'])
        with self.assertRaises(Problem):self.engine.packet(t['id'])
        with self.assertRaises(Problem):self.engine.complete(t['id'],{})
    def test_view_rejects_wrong_page_or_forged_membership(self):
        for bad in [view(['101'])|{'url':'https://x.com/home'},view(['101'])|{'posts':[]},view(['101'])|{'root_ids':[101]}]:
            with self.assertRaises(Problem):bookmarks.save_view(self.engine,bad,'reader')
        result=bookmarks.save_view(self.engine,view(['101']),'reader')
        self.assertEqual(result['bookmark_ids'],['101'])
    def test_cached_images_survive_bookmark_observation_without_bytes(self):
        original=post();original['media']=[{'url':'https://pbs.twimg.com/media/synthetic.png','data':base64.b64encode(PNG).decode()}]
        a=self.store.capture(original)
        original['media'][0].pop('data');b=self.store.capture(original)
        self.assertEqual(a['revision'],b['revision']);self.assertEqual(self.store.get(PID)['source']['coverage']['images_saved'],1)
    def test_archive_cache_is_reused_only_after_live_membership_observation(self):
        p=post();p['media']=[{'url':'https://pbs.twimg.com/media/synthetic.png','data':base64.b64encode(PNG).decode()}]
        self.store.capture(p);self.store.mark_bookmarks('archive:synthetic',[PID],'personal_ingestion')
        self.assertFalse(self.store.bookmark_ids('reader'))
        p['media'][0].pop('data');page=view([PID]);page['posts']=[p]
        t,calls=self.run_sync([page])
        self.assertEqual(t['body']['bookmark_result']['reused'],1)
        self.assertEqual(t['body']['bookmark_result']['saved'],0)
        self.assertEqual(self.store.bookmark_ids('reader'),{PID})
        self.assertTrue(all(a.get('metadata_only') for operation,a in calls if operation=='bookmarks'))
        self.assertFalse(any(operation=='read_post' for operation,a in calls))
    def test_budget_does_not_download_images_of_unprocessed_bookmarks(self):
        page=view(['101','102'])
        for p in page['posts']:p['media']=[{'url':'https://pbs.twimg.com/media/'+p['id']+'.png'}]
        calls=[]
        def browser(operation,args,cancel):
            calls.append((operation,args))
            if operation=='read_post':return self.engine.save({'posts':[post(args['id'])]})
            if args['mode']=='current':
                self.assertIn('102',args['skip_ids']);self.assertNotIn('101',args['skip_ids'])
                page['posts'][0]['media'][0]['data']=base64.b64encode(PNG).decode()
            return page
        self.engine.browser=browser;t=bookmarks.create_sync(self.store,{'mode':'backfill','max_items':1,'max_scrolls':1,'max_seconds':30});self.store.update_task(t['id'],'running')
        bookmarks.sync(self.engine,t['id'],time.monotonic()+10)
        self.assertEqual(self.store.bookmark_ids('reader'),{'101'});self.assertEqual(self.store.setting('bookmark_backfill:reader')['anchor'],'101')
    def test_local_archive_import_media_historical_time_and_idempotency(self):
        archive=self.home/'old'/'posts';archive.mkdir(parents=True);media_root=self.home/'old'/'attachments';media_root.mkdir()
        image=media_root/'sample.png';image.write_bytes(PNG);url='https://pbs.twimg.com/media/synthetic.png'
        value={'post_id':PID,'text':'旧库原文','author':{'username':'writer'},'media':[{'type':'photo','url':url}],
            'reader_document':{'metadata':{'extraction_status':'complete','archived_at':'2026-01-01','attachments':[str(image)]}},
            'projection_fingerprint':{'dependencies':{'media':[{'url_sha256':hashlib.sha256(url.encode()).hexdigest(),'relative_path':'sample.png','sha256':hashlib.sha256(PNG).hexdigest()}]}}}
        file=archive/(PID+'.json');file.write_text(json.dumps(value),encoding='utf-8');before=file.read_bytes()
        a=bookmarks.import_archive(self.store,archive,media_root);self.assertFalse(a['errors']);self.assertEqual(a['photos'],1)
        self.store.annotate(PID,'保留笔记',['研究']);b=bookmarks.import_archive(self.store,archive,media_root)
        self.assertEqual(b['imported'],1);self.assertEqual(len(self.store.get(PID)['versions']),1);self.assertEqual(file.read_bytes(),before)
        self.assertEqual(self.store.get(PID)['source']['observed_at'],'2026-01-01');self.assertEqual(self.store.get(PID)['note'],'保留笔记')
        value['projection_fingerprint']['dependencies']['media'][0]['relative_path']='../escape.png';file.write_text(json.dumps(value),encoding='utf-8')
        self.assertIn('越界',bookmarks.import_archive(self.store,archive,media_root)['errors'][0]['error'])
    def test_cli_store_open_does_not_interrupt_running_server_tasks(self):
        t=self.store.create_task({'topic':'活跃任务'});self.store.update_task(t['id'],'running')
        Store(self.home,recover=False);self.assertEqual(self.store.task_state(t['id']),'running')
    def test_mcp_exposes_bookmark_sync_without_local_path_import(self):
        result=mcp.response({'jsonrpc':'2.0','id':1,'method':'tools/list'},8915)['result']['tools']
        names={x['name'] for x in result};self.assertIn('x_create_bookmark_sync',names);self.assertNotIn('import_archive',names)
    def test_real_bridge_worker_sync_and_stop_never_invokes_model(self):
        b=self.engine.bridge;del b.status
        origin='chrome-extension://'+'a'*32;key=b.auth(origin,b.pair(origin,b.code()['code'])['token'])
        binding={'session':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','tab_id':7,'account':'reader','revision':1,'version':'0.3.0','grant_at':time.time()+.01,'control':True}
        b.poll(key,{'binding':binding});stopped=threading.Event();actions=[]
        def extension():
            while not stopped.is_set():
                command=b.poll(key,{'binding':binding})
                if command and command.get('id'):
                    actions.append(command['action']);b.result(key,{'id':command['id'],'session':binding['session'],'result':view([PID])})
                time.sleep(.005)
        self.engine.generate=lambda *a: self.fail('bookmarks must not invoke a model')
        thread=threading.Thread(target=extension);thread.start()
        try:
            t=bookmarks.create_sync(self.store,{'max_scrolls':4,'max_seconds':30});self.engine.run(t['id'])
            end=time.monotonic()+5
            while t['id'] in self.engine.running and time.monotonic()<end:time.sleep(.01)
            t=self.store.task(t['id']);self.assertEqual(t['state'],'completed',t['body'].get('error'))
            self.assertEqual(set(actions),{'bookmarks'});self.assertEqual(self.store.bookmark_ids('reader'),{PID})
            b.stop()
            with self.assertRaises(Problem):self.engine.browser('bookmarks',{})
        finally:stopped.set();thread.join()
    def test_manual_bookmark_capture_through_http_pairing_and_library_filter(self):
        server=make_server(self.store,0);thread=threading.Thread(target=server.serve_forever);thread.start()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:'+str(server.server_port)
        def request(path,body=None,headers=None):
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**(headers or {})})
            with opener.open(req,timeout=3) as response:return json.load(response)
        try:
            csrf=request('/api/state')['csrf'];code=request('/api/action',{'action':'pair'},{'X-CSRF':csrf})['result']['code']
            origin='chrome-extension://'+'a'*32;token=request('/bridge/pair',{'code':code},{'Origin':origin})['result']['token'];headers={'Origin':origin,'Authorization':'Bearer '+token}
            binding={'session':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','tab_id':7,'account':'reader','revision':1,'control':False,'grant_at':0}
            request('/bridge/poll',{'binding':binding},headers)
            with self.assertRaises(urllib.error.HTTPError):request('/bridge/capture',view(['102'],account='other')|{'binding':binding},headers)
            result=request('/bridge/capture',view([PID])|{'binding':binding},headers)['result']
            self.assertEqual(result['bookmark_ids'],[PID]);self.assertEqual(request('/api/library?collection=bookmarks')['total'],1)
            self.assertEqual(request('/api/library?collection=bookmarks&owner=other')['total'],0)
        finally:server.shutdown();thread.join();server.engine.close();server.server_close()

if __name__=='__main__':unittest.main()

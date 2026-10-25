"""书签观察、有限批次同步和旧本地归档导入；不改动线上收藏。"""
import base64
import json
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from .store import Problem, post_id, now, sha


def create_sync(store,args):
    mode=args.get('mode','recent')
    if mode not in ('recent','backfill'): raise Problem('书签模式须为 recent 或 backfill')
    reset=args.get('reset_position',False)
    if type(reset) is not bool or reset and mode!='backfill':raise Problem('从顶部重扫只适用于历史书签')
    def bound(key,default,maximum):
        value=args.get(key,default)
        if type(value) is not int or not 1<=value<=maximum: raise Problem('书签预算无效：'+key)
        return value
    return store.create_task({'kind':'bookmarks','topic':'书签 · '+('检查新增' if mode=='recent' else '继续历史'),
        'mode':mode,'reset_position':reset,'max_items':bound('max_items',100,1000),'max_scrolls':bound('max_scrolls',30,500),
        'max_seconds':bound('max_seconds',600,1800),'sources':[]})


def validate_view(result,owner):
    if not isinstance(result,dict) or result.get('account')!=owner: raise Problem('书签账号无法核对，已保留进度')
    url=urlsplit(result.get('url',''))
    if url.scheme!='https' or url.username or url.password or url.hostname not in ('x.com','twitter.com') or url.path.rstrip('/')!='/i/bookmarks': raise Problem('返回内容不是全部书签页')
    ids=result.get('root_ids')
    if not isinstance(ids,list) or len(ids)>80: raise Problem('书签列表格式无效')
    for pid in ids: post_id(pid)
    posts=result.get('posts')
    if not isinstance(posts,list) or len(posts)>160: raise Problem('书签正文格式无效')
    for post in posts:
        if not isinstance(post,dict):raise Problem('书签正文格式无效')
        post_id(post.get('id'))
    if not set(ids).issubset({p['id'] for p in posts}): raise Problem('书签列表与正文不一致')
    return list(dict.fromkeys(ids))


def save_view(engine,result,owner):
    ids=validate_view(result,owner)
    saved={'saved':[]}
    for offset in range(0,len(result['posts']),80): saved['saved'].extend(engine.save({'posts':result['posts'][offset:offset+80]})['saved'])
    engine.store.mark_bookmarks(owner,ids)
    return saved|{'bookmark_ids':ids,'scope':result.get('scope','已加载部分')}


def sync(engine,tid,deadline):
    store=engine.store;t=store.task(tid)['body']
    active=[c for c in engine.bridge.status() if c['online'] and c['control']]
    if len(active)!=1: raise Problem('书签同步需要一个已连接并开启接管的 X 标签页')
    owner=active[0]['account']
    if t.get('owner',owner)!=owner: raise Problem('该批次属于另一账号；不会把两个账号的书签混在一起')
    mode=t['mode'];cursor_key='bookmark_backfill:'+owner
    progress=t.get('bookmark_progress') or {'anchor':(store.setting(cursor_key,{}) or {}).get('anchor') if mode=='backfill' and not t.get('reset_position') else None,'queue':[],'pages':0}
    refs={r['id']:r for r in t.get('sources',[])};known=store.bookmark_ids(owner)
    old=set(known);visited=set();observed=set();reason='scroll_budget';empty=False;unavailable=0;attempted=set();reused=0
    def checkpoint():
        store.update_task(tid,'running',{'owner':owner,'bookmark_progress':progress,'sources':list(refs.values())},expected=('running',))
    def stopped(): return engine.cancelled(tid) or time.monotonic()>=deadline
    def capture_pending():
        for item in progress['queue']:
            if item['state']=='done' or item['id'] in attempted: continue
            if stopped(): return False
            attempted.add(item['id'])
            try:
                saved=engine.browser('read_post',{'id':item['id']},stopped)
                root=next((p for p in saved['saved'] if p['id']==item['id']),None)
                if not root: raise Problem('未读到书签对应原帖')
                source=store.get(root['id'],root['selected_revision'])['source']
                if not source['coverage'].get('text_complete') or any(m.get('status')!='saved' for m in source['media']):
                    item.update(state='partial',error='正文或图片仍不完整；保留原始覆盖，后续可重试')
                else: item.update(state='done',error='')
                refs[item['id']]={'id':root['id'],'revision':root['selected_revision'],'origin':'bookmark'};checkpoint()
            except Exception as e:
                item.update(state='pending',error=str(e)[:500]);checkpoint();raise
        return True
    checkpoint()
    if not capture_pending(): raise Problem('本轮时间到期；未读书签进度已保存')
    anchor=progress.get('anchor');seeking=bool(anchor);stale=0;overlap=0;last_signature=None;last_position=None
    continued_scrolls=0;seek_scrolls=0
    # Reopen once, scan continuously, then read incomplete details. No list/detail
    # round trip per bookmark. Resumption seeks the saved ID, never trusts pixels.
    current_url=urlsplit(active[0].get('url') or '')
    warm=bool(seeking and current_url.scheme=='https' and current_url.hostname in ('x.com','twitter.com') and current_url.path.rstrip('/')=='/i/bookmarks')
    result=engine.browser('bookmarks',{'mode':'open','reset':not warm,'metadata_only':True},stopped)
    if warm and anchor not in validate_view(result,owner):
        result=engine.browser('bookmarks',{'mode':'open','reset':True,'metadata_only':True},stopped)
    while True:
        if stopped(): reason='seek_time_budget' if seeking else 'time_budget';break
        ids=validate_view(result,owner);empty=result.get('empty') is True and not ids
        unavailable=max(unavailable,int(result.get('unavailable',0)))
        if empty:
            if mode=='backfill' and t.get('reset_position'):store.configure(cursor_key,{'anchor':None,'scope':'empty_observed','observed_at':now()})
            reason='empty_observed';break
        signature=tuple(ids);newly_observed=set(ids)-observed;observed.update(ids)
        position=(result.get('position') or {}).get('y')
        stale=stale+1 if signature==last_signature and not newly_observed and position==last_position else 0
        last_signature=signature;last_position=position
        if stale>=3: reason='stalled';break
        eligible=ids
        if seeking:
            if anchor in ids:
                seeking=False;eligible=ids[ids.index(anchor)+1:]
            else:
                # Seeking a known position must not consume the new-page budget.
                # It is bounded by the shared deadline and a hard operation cap.
                if seek_scrolls>=5000:reason='anchor_not_found';break
                seek_scrolls+=1
                result=engine.browser('bookmarks',{'mode':'scroll','metadata_only':True},stopped)
                continue
        fresh=[pid for pid in eligible if pid not in visited];visited.update(eligible)
        candidates={p['id']:p for p in result['posts']}
        reusable=set()
        for pid in fresh:
            try:
                source=store.get(pid)['source'];raw=candidates[pid]
                same_text=source['text']==raw['text'] or not raw['coverage'].get('text_complete') and bool(raw['text']) and source['text'].startswith(raw['text'].rstrip('…'))
                if source['coverage'].get('text_complete') and same_text and {m['url'] for m in source['media']}=={m['url'] for m in raw.get('media',[])} and all(m.get('status')=='saved' for m in source['media']):
                    for media in source['media']:store.media_file(media)
                    reusable.add(pid)
            except Problem:pass
        new_roots=[pid for pid in fresh if pid not in reusable][:max(0,t['max_items']-len(progress['queue']))]
        needs_images={pid for pid in new_roots if candidates[pid].get('media')}
        for pid in new_roots:
            for relation in candidates[pid].get('relations',[]):
                qid=relation.get('target')
                if relation.get('type')=='quotes' and qid in candidates and candidates[qid].get('media'):needs_images.add(qid)
        if needs_images:
            full=engine.browser('bookmarks',{'mode':'current','skip_ids':[pid for pid in candidates if pid not in needs_images]},stopped)
            if validate_view(full,owner)!=ids:raise Problem('下载图片时书签列表变化；保留已处理位置，未错配原文')
            candidates={p['id']:p for p in full['posts']}
        page_known=bool(ids) and all(pid in old for pid in ids)
        overlap=overlap+1 if page_known else 0
        last_processed=None
        for pid in fresh:
            if pid in reusable:
                store.mark_bookmarks(owner,[pid]);known.add(pid);reused+=1;last_processed=pid;continue
            if len(progress['queue'])>=t['max_items']: reason='item_budget';break
            raw=candidates[pid];saved=store.capture(raw);store.mark_bookmarks(owner,[pid]);known.add(pid)
            refs[pid]={'id':pid,'revision':saved['selected_revision'],'origin':'bookmark'}
            if not any(q['id']==pid for q in progress['queue']):
                progress['queue'].append({'id':pid,'state':'done' if raw['coverage'].get('text_complete') and all(m.get('data') for m in raw.get('media',[])) else 'pending'})
            for relation in raw.get('relations',[]):
                if relation['type']=='quotes' and relation['target'] in candidates and relation['target'] not in known:
                    store.capture(candidates[relation['target']])
            last_processed=pid
        # Do not overwrite the history anchor while still searching for it.
        if not seeking and last_processed:
            progress.update(anchor=last_processed,pages=progress.get('pages',0)+1)
            if mode=='backfill': store.configure(cursor_key,{'anchor':last_processed,'observed_at':now(),'scope':'observed_partial'})
        checkpoint()
        if reason=='item_budget':break
        if mode=='recent' and overlap>=2: reason='recent_overlap';break
        if continued_scrolls>=t['max_scrolls']:break
        continued_scrolls+=1
        result=engine.browser('bookmarks',{'mode':'scroll','metadata_only':True},stopped)
    if seeking and reason!='seek_time_budget': reason='anchor_not_found'
    if not capture_pending(): reason='time_budget'
    if engine.cancelled(tid): return
    partial=sum(x['state']!='done' for x in progress['queue'])
    summary={'reason':reason,'saved':len(progress['queue']),'reused':reused,'partial':partial,'observed':len(observed),'seek_scrolls':seek_scrolls,'continued_scrolls':continued_scrolls,'unavailable_observed':unavailable,
        'scope':'本轮可见书签；不代表全部历史。未出现的旧记录保留，检查新增不覆盖历史位置。'}
    store.update_task(tid,'completed',{'bookmark_result':summary},expected=('running',))
    store.event(tid,'书签批次结束：'+reason+'；资料已落盘，覆盖边界已保留')


def import_archive(store,posts_dir,attachments_dir=None):
    """Explicit local CLI import. Legacy source files remain read-only."""
    root=Path(posts_dir).resolve()
    if not root.is_dir(): raise Problem('旧书签目录不存在')
    attachments=Path(attachments_dir).resolve() if attachments_dir else None
    owner='archive:'+sha(str(root))[:16];chosen={};errors=[]
    for file in root.rglob('*.json'):
        if file.is_symlink() or not file.resolve().is_relative_to(root):continue
        match=re.search(r'(\d{1,25})(?:\])?\.json$',file.name)
        if not match:continue
        pid=match[1];score=(bool(re.search(r'-\[\d+\]\.json$',file.name)),file.stat().st_mtime_ns)
        if pid not in chosen or score>chosen[pid][0]:chosen[pid]=(score,file)
    count=0;photos=0;bytes_copied=0
    for expected,(_,file) in chosen.items():
        try:
            if file.stat().st_size>12_000_000:raise Problem('单条归档超出12MB限制')
            v=json.loads(file.read_text(encoding='utf-8-sig'));pid=post_id(v.get('post_id'))
            if pid!=expected:raise Problem('文件名和原帖 ID 不一致')
            metadata=(v.get('reader_document') or {}).get('metadata') or {};author=v.get('author') or {}
            text=v.get('text') or '';article=v.get('article') or {};article_text=article.get('plain_text') or ''
            if article_text and article_text not in text:text+='\n\n'+article.get('title','')+'\n'+article_text
            relations=[{'type':kind,'target':post_id(v[key]),'evidence':'personal_ingestion_archive'} for key,kind in [('in_reply_to_post_id','reply_to'),('quoted_post_id','quotes')] if v.get(key)]
            media=[];total=0
            dependencies={d.get('url_sha256'):d for d in (v.get('projection_fingerprint') or {}).get('dependencies',{}).get('media',[]) if isinstance(d,dict)}
            for index,item in enumerate(v.get('media') or []):
                if not isinstance(item,dict) or item.get('type') not in ('photo','image',None):continue
                url=item.get('url') or item.get('source_url') or ''
                if urlsplit(url).scheme!='https' or urlsplit(url).hostname!='pbs.twimg.com':continue
                dependency=dependencies.get(hashlib.sha256(url.encode()).hexdigest(),{})
                path=dependency.get('relative_path') or item.get('local_path') or item.get('path') or ''
                registered=metadata.get('attachments') or []
                if not path and index<len(registered) and isinstance(registered[index],str):path=registered[index]
                m={'url':url,'alt':item.get('alt',''),'error':'旧库附件未导入'}
                if attachments and path:
                    source=(attachments/path).resolve()
                    if not source.is_relative_to(attachments):raise Problem('旧库附件路径越界')
                    if source.is_file() and source.stat().st_size<=8_000_000 and total+source.stat().st_size<=24_000_000:
                        payload=source.read_bytes()
                        if dependency.get('sha256') and hashlib.sha256(payload).hexdigest()!=dependency['sha256']:raise Problem('旧库图片与登记哈希不符')
                        m['data']=base64.b64encode(payload).decode();photos+=1;bytes_copied+=len(payload);total+=len(payload)
                media.append(m)
            status=metadata.get('extraction_status','unknown')
            raw={'id':pid,'text':text,'author':{'id':str(author.get('id') or ''),'handle':author.get('username') or author.get('handle') or '', 'name':author.get('display_name') or author.get('name') or ''},
                'published_at':v.get('published_at'),'observed_at':metadata.get('archived_at') or 'unknown', 'links':v.get('external_urls') or [],'relations':relations,'media':media[:20],
                'coverage':{'text_complete':status=='complete','method':'personal_ingestion_archive','archive_status':status,'thread':'not_collected','article':'archived_text' if article_text else 'not_detected','bookmark_status':'historical_observation','archive_source':str(file)},'extraction':{'importer':'personal_ingestion_v1'}}
            store.capture(raw,select_latest=False)
            quote=v.get('quote') or {};qid=quote.get('post_id') or quote.get('id')
            if qid and isinstance(quote.get('text'),str):
                qa=quote.get('author') or {}
                store.capture({'id':post_id(qid),'text':quote['text'],'author':{'id':str(qa.get('id') or ''),'handle':qa.get('username') or qa.get('handle') or '', 'name':qa.get('display_name') or qa.get('name') or ''},'published_at':quote.get('published_at'),'observed_at':raw['observed_at'],'relations':[],'media':[], 'coverage':{'text_complete':False,'method':'personal_ingestion_quote','thread':'quoted_context','archive_source':str(file)}},select_latest=False)
            store.mark_bookmarks(owner,[pid],'personal_ingestion',metadata.get('archived_at') or 'unknown');count+=1
        except (Problem,OSError,ValueError,TypeError,KeyError) as e:errors.append({'file':file.name,'error':str(e)[:250]})
    return {'imported':count,'owner':owner,'photos':photos,'bytes_read':bytes_copied,'errors':errors,'scope':'旧库历史书签；未访问 X，不能证明当前在线书签状态'}

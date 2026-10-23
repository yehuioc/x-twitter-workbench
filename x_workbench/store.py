from __future__ import annotations
import base64
import contextlib
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / 'data'
class Problem(ValueError): pass
def now(): return datetime.now(timezone.utc).isoformat()
def dumps(value): return json.dumps(value, ensure_ascii=False, sort_keys=True)
def sha(value): return hashlib.sha256(dumps(value).encode()).hexdigest()
def post_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,25}', value):
        raise Problem('帖子 ID 必须是十进制字符串，不能作为浮点数传入')
    return value
def canonical(pid): return 'https://x.com/i/status/' + post_id(pid)
def media_type(payload):
    if payload.startswith(b'\x89PNG\r\n\x1a\n'): return 'png', 'image/png'
    if payload.startswith(b'\xff\xd8\xff'): return 'jpg', 'image/jpeg'
    if payload.startswith((b'GIF87a', b'GIF89a')): return 'gif', 'image/gif'
    if payload[:4] == b'RIFF' and payload[8:12] == b'WEBP': return 'webp', 'image/webp'
    raise Problem('附件不是允许的图片格式')

class Store:
    def __init__(self, home=HOME):
        self.home = Path(home).resolve()
        if not self.home.is_relative_to(ROOT): raise Problem('资料必须保存在项目内')
        self.home.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.path = self.home / 'library.sqlite'
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, latest TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots(revision TEXT PRIMARY KEY, post_id TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, task_id TEXT, message TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS keys(hash TEXT PRIMARY KEY, origin TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS snapshots_post_created ON snapshots(post_id,created DESC);
            CREATE INDEX IF NOT EXISTS posts_seen ON posts(last_seen DESC,id);
            CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated DESC);
            CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
            CREATE TABLE IF NOT EXISTS search_docs(post_id TEXT PRIMARY KEY, original TEXT NOT NULL, model TEXT NOT NULL DEFAULT '', text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_sources(task_id TEXT NOT NULL,post_id TEXT NOT NULL,PRIMARY KEY(task_id,post_id));
            CREATE INDEX IF NOT EXISTS task_sources_post ON task_sources(post_id,task_id);
            ''')
            self.fts = True
            try: db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(post_id UNINDEXED,text,tokenize="trigram")')
            except sqlite3.OperationalError: self.fts = False
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                for row in db.execute('SELECT id,body FROM tasks').fetchall():
                    self._index_task(db,row['id'],json.loads(row['body']))
                for row in db.execute('SELECT id FROM posts').fetchall(): self._index_post(db,row['id'])
                db.execute('PRAGMA user_version=1')
            elif self.fts and db.execute('SELECT count(*) FROM search_fts').fetchone()[0]!=db.execute('SELECT count(*) FROM search_docs').fetchone()[0]:
                db.execute('DELETE FROM search_fts')
                db.execute('INSERT INTO search_fts(rowid,post_id,text) SELECT p.rowid,d.post_id,d.text FROM search_docs d JOIN posts p ON p.id=d.post_id')
            db.execute("UPDATE tasks SET state='interrupted' WHERE state IN ('running','collecting','generating')")
    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def setting(self, key, default=None):
        with self.db() as db: row = db.execute('SELECT body FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    def configure(self, key, value):
        with self.db() as db: db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, dumps(value)))
    def _index_post(self,db,pid):
        row=db.execute('SELECT p.rowid AS rid,p.note,p.tags,s.body FROM posts p JOIN snapshots s ON s.revision=p.latest WHERE p.id=?',(pid,)).fetchone()
        if not row: return
        source=json.loads(row['body']); author=source.get('author') or {}
        original=source.get('text','')+str(author.get('handle',''))+str(author.get('name',''))+row['note']+row['tags']
        models=db.execute("SELECT t.body FROM task_sources r JOIN tasks t ON t.id=r.task_id WHERE r.post_id=?",(pid,)).fetchall()
        model='\n'.join(dumps(json.loads(r['body']).get('result','')) for r in models)
        text=original+'\n'+model
        db.execute('INSERT OR REPLACE INTO search_docs VALUES(?,?,?,?)',(pid,original,model,text))
        if self.fts:
            db.execute('DELETE FROM search_fts WHERE rowid=?',(row['rid'],))
            db.execute('INSERT INTO search_fts(rowid,post_id,text) VALUES(?,?,?)',(row['rid'],pid,text))
    def _index_task(self,db,tid,body):
        old={r[0] for r in db.execute('SELECT post_id FROM task_sources WHERE task_id=?',(tid,))}
        db.execute('DELETE FROM task_sources WHERE task_id=?',(tid,))
        ids={r['id'] for r in body.get('sources',[]) if isinstance(r,dict) and isinstance(r.get('id'),str)} if body.get('result') else set()
        db.executemany('INSERT OR IGNORE INTO task_sources VALUES(?,?)',[(tid,pid) for pid in ids])
        for pid in old|ids: self._index_post(db,pid)
    def capture(self, raw):
        if not isinstance(raw, dict): raise Problem('原文须为对象')
        pid = post_id(raw.get('id'))
        text = raw.get('text', '')
        if not isinstance(text, str) or len(text) > 250000: raise Problem('正文格式或大小无效')
        author = raw.get('author', {})
        if not isinstance(author, dict): raise Problem('作者格式无效')
        body = {k: raw.get(k) for k in ('author','published_at','lang','metrics','relations','coverage','extraction','links')}
        body.update(id=pid, url=canonical(pid), text=text, author=author,
                    observed_at=raw.get('observed_at') or now(), media=[])
        if not isinstance(body['coverage'], dict): raise Problem('必须说明读取覆盖')
        body['coverage'] = dict(body['coverage'])
        relations = body['relations'] or []
        if not isinstance(relations, list) or len(relations) > 100: raise Problem('关系数据无效')
        for rel in relations:
            if not isinstance(rel, dict) or rel.get('type') not in ('reply_to','quotes','reposts','candidate_thread_member'):
                raise Problem('关系类型无效')
            post_id(rel.get('target'))
        body['relations'] = relations
        items = raw.get('media') or []
        if not isinstance(items, list) or len(items) > 20: raise Problem('图片数量超出本轮限制')
        total = 0
        for item in items:
            url = item.get('url', '')
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname != 'pbs.twimg.com': raise Problem('图片来源域名未授权')
            entry = {k: item.get(k) for k in ('url','alt','status','error')}
            encoded = item.get('data')
            if encoded:
                try: payload = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as e: raise Problem('图片编码无效') from e
                total += len(payload)
                if len(payload) > 8_000_000 or total > 24_000_000: raise Problem('图片大小超出保存限制')
                ext, mime = media_type(payload)
                digest = hashlib.sha256(payload).hexdigest()
                target = self.home / 'media' / (digest + '.' + ext)
                target.parent.mkdir(exist_ok=True)
                if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest: target.write_bytes(payload)
                entry.update(file=str(target.relative_to(self.home)).replace('\\','/'), sha256=digest, mime=mime, status='saved')
            else:
                entry.update(status='missing', error=item.get('error') or '未取得图片字节')
            body['media'].append(entry)
        body['coverage']['images_saved'] = sum(x['status'] == 'saved' for x in body['media'])
        # Capture identity excludes observation time and counters; repeat saves do not duplicate.
        identity = {k: body[k] for k in ('id','text','author','relations','media','coverage','published_at')}
        revision = sha(identity)
        with self.lock, self.db() as db:
            old = db.execute('SELECT latest FROM posts WHERE id=?', (pid,)).fetchone()
            db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)', (revision, pid, dumps(body), now()))
            select = revision
            if old:
                previous = json.loads(db.execute('SELECT body FROM snapshots WHERE revision=?', (old[0],)).fetchone()[0])
                if previous['coverage'].get('text_complete') is True and body['coverage'].get('text_complete') is not True:
                    select = old[0]
                db.execute('UPDATE posts SET latest=?,last_seen=? WHERE id=?', (select, now(), pid))
            else: db.execute('INSERT INTO posts(id,latest,first_seen,last_seen) VALUES(?,?,?,?)', (pid, revision, now(), now()))
            self._index_post(db,pid)
        return {'id':pid,'revision':revision,'selected_revision':select,'coverage':body['coverage']}
    def media_file(self,entry):
        path=(self.home/entry.get('file','')).resolve()
        if not path.is_relative_to(self.home/'media') or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=entry.get('sha256'):
            raise Problem('本地图片缺失、改变或越界；请重新采集核对')
        return path
    def get(self, pid, revision=None):
        post_id(pid)
        with self.db() as db:
            p = db.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
            if not p: raise Problem('帖子尚未保存')
            s = db.execute('SELECT * FROM snapshots WHERE revision=? AND post_id=?', (revision or p['latest'], pid)).fetchone()
            if not s: raise Problem('原文版本不存在或不属于此帖')
            versions = db.execute('SELECT revision,created FROM snapshots WHERE post_id=? ORDER BY created DESC', (pid,)).fetchall()
        return dict(p) | {'tags':json.loads(p['tags']),'revision':s['revision'],'source':json.loads(s['body']),'versions':[dict(x) for x in versions]}
    def annotate(self, pid, note, tags):
        self.get(pid)
        if not isinstance(note, str) or len(note)>100000 or not isinstance(tags,list) or len(tags)>100 or any(not isinstance(t,str) or len(t)>100 for t in tags): raise Problem('笔记或标签格式无效')
        with self.lock,self.db() as db:
            db.execute('UPDATE posts SET note=?,tags=? WHERE id=?', (note, dumps(list(dict.fromkeys(t.strip() for t in tags if t.strip()))), pid))
            self._index_post(db,pid)
        return self.get(pid)
    def search(self, query='', tag='', offset=0, limit=50, brief=False):
        if not isinstance(query,str) or len(query)>500: raise Problem('检索词无效')
        offset, limit = max(0,int(offset)), min(100,max(1,int(limit)))
        clauses, args = [], []
        indexed=[token for token in query.split() if len(token)>=3] if self.fts else []
        if indexed:
            clauses.append('p.id IN (SELECT post_id FROM search_fts WHERE search_fts MATCH ?)')
            args.append(' AND '.join('"'+token.replace('"','""')+'"' for token in indexed))
        # Exact checks preserve short Chinese terms and literal punctuation semantics.
        for token in query.split():
            clauses.append('(instr(lower(d.original),lower(?))>0 OR instr(lower(d.model),lower(?))>0)')
            args.extend([token,token])
        if tag:
            clauses.append('EXISTS(SELECT 1 FROM json_each(p.tags) WHERE value=?)'); args.append(tag)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.db() as db:
            joined=' FROM posts p JOIN search_docs d ON d.post_id=p.id'
            total = db.execute('SELECT count(*)'+joined+where,args).fetchone()[0]
            rows = db.execute('SELECT p.id'+joined+where+' ORDER BY p.last_seen DESC,p.id LIMIT ? OFFSET ?', args+[limit,offset]).fetchall()
            ids=[r[0] for r in rows]; items=[]
            if ids:
                marks=','.join('?' for _ in ids)
                if brief:
                    rows=db.execute("SELECT p.id,p.tags,p.latest AS revision,json_extract(s.body,'$.author') AS author,substr(json_extract(s.body,'$.text'),1,240) AS text,json_extract(s.body,'$.coverage') AS coverage,json_extract(s.body,'$.observed_at') AS observed_at FROM posts p JOIN snapshots s ON s.revision=p.latest WHERE p.id IN ("+marks+')',ids).fetchall()
                    found={r['id']:{'id':r['id'],'revision':r['revision'],'tags':json.loads(r['tags']),'source':{'author':json.loads(r['author'] or '{}'),'text':r['text'],'coverage':json.loads(r['coverage']),'observed_at':r['observed_at']}} for r in rows}
                else:
                    rows=db.execute('SELECT p.*,s.body FROM posts p JOIN snapshots s ON s.revision=p.latest WHERE p.id IN ('+marks+')',ids).fetchall()
                    versions={pid:[] for pid in ids}
                    for r in db.execute('SELECT post_id,revision,created FROM snapshots WHERE post_id IN ('+marks+') ORDER BY created DESC',ids): versions[r['post_id']].append({'revision':r['revision'],'created':r['created']})
                    found={r['id']:{k:r[k] for k in r.keys() if k!='body'}|{'tags':json.loads(r['tags']),'revision':r['latest'],'source':json.loads(r['body']),'versions':versions[r['id']]} for r in rows}
                items=[found[pid] for pid in ids]
        return {'items':items,'total':total,'offset':offset,'limit':limit}
    def local_sources(self,topic,keywords,limit=10,threads=False):
        queries=list(dict.fromkeys(keywords or [topic])); found={}
        for query in queries:
            if not isinstance(query,str) or not query.strip(): continue
            # Advanced X operators cannot be faithfully evaluated on a partial local library.
            if re.search(r'\b(?:to|since|until|filter|lang|has|min_faves):',query,re.I): continue
            author=re.search(r'\bfrom:([A-Za-z0-9_]{1,15})',query,re.I)
            words=re.sub(r'\bfrom:[A-Za-z0-9_]{1,15}', '',query,flags=re.I).strip()
            if re.search(r'\b\w+:',words): continue
            lookup=' '.join(x for x in (words,author.group(1) if author else '') if x)
            for p in self.search(lookup,limit=100)['items']:
                s=p['source']; coverage=s['coverage']
                if author and s['author'].get('handle','').lower()!=author.group(1).lower(): continue
                if coverage.get('text_complete') is not True or threads and not coverage.get('thread_ids'): continue
                try:
                    if any(not m.get('file') for m in s['media']): continue
                    for m in s['media']: self.media_file(m)
                except Problem: continue
                found.setdefault(p['id'],p)
                if len(found)>=limit: return list(found.values())
        return list(found.values())
    def task_summaries(self):
        with self.db() as db:
            rows=db.execute("SELECT id,state,updated,json_extract(body,'$.topic') AS topic,json_extract(body,'$.sources') AS sources,json_type(body,'$.result') IS NOT NULL AS has_result FROM tasks ORDER BY updated DESC LIMIT 100").fetchall()
        return [{'id':r['id'],'state':r['state'],'updated':r['updated'],'has_result':bool(r['has_result']),'body':{'topic':r['topic'],'sources':json.loads(r['sources'] or '[]')}} for r in rows]
    def task_state(self,tid):
        with self.db() as db: row=db.execute('SELECT state FROM tasks WHERE id=?',(tid,)).fetchone()
        if not row: raise Problem('任务不存在')
        return row[0]
    def task(self, tid):
        with self.db() as db:
            row=db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
            events=db.execute('SELECT message,created FROM events WHERE task_id=? ORDER BY id',(tid,)).fetchall()
        if not row: raise Problem('任务不存在')
        return dict(row)|{'body':json.loads(row['body']),'events':[dict(x) for x in events]}
    def tasks(self):
        with self.db() as db: rows=db.execute('SELECT id FROM tasks ORDER BY updated DESC LIMIT 100').fetchall()
        return [self.task(r[0]) for r in rows]
    def create_task(self, body):
        tid=uuid.uuid4().hex
        with self.db() as db: db.execute('INSERT INTO tasks VALUES(?,?,?,?)',(tid,'queued',dumps(body),now()))
        return self.task(tid)
    def update_task(self, tid, state, patch=None, expected=None):
        with self.lock, self.db() as db:
            row=db.execute('SELECT state,body FROM tasks WHERE id=?',(tid,)).fetchone()
            if not row: raise Problem('任务不存在')
            if expected and row[0] not in expected: raise Problem('任务已停止或状态已改变')
            body=json.loads(row[1]); body.update(patch or {})
            db.execute('UPDATE tasks SET state=?,body=?,updated=? WHERE id=?',(state,dumps(body),now(),tid))
            if patch and ('result' in patch or body.get('result') and 'sources' in patch): self._index_task(db,tid,body)
        return self.task(tid)
    def event(self, tid, message):
        with self.db() as db:
            db.execute('INSERT INTO events(task_id,message,created) VALUES(?,?,?)',(tid,str(message)[:2000],now()))
            db.execute('UPDATE tasks SET updated=? WHERE id=?',(now(),tid))

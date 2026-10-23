"""测试当前查询，可选择公开 Git 基线对比；只构造内存合成样本。"""
import argparse
import contextlib
import json
import sqlite3
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from x_workbench.store import Store

def run(size,baseline):
    module=None
    if baseline:
        module=types.ModuleType('baseline_store');module.__file__=str(ROOT/'x_workbench'/'store.py')
        source=subprocess.check_output(['git','show',baseline+':x_workbench/store.py'],cwd=ROOT)
        exec(compile(source,module.__file__,'exec'),module.__dict__)
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE posts(id TEXT PRIMARY KEY,latest TEXT NOT NULL,note TEXT NOT NULL,tags TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL);
        CREATE TABLE snapshots(revision TEXT PRIMARY KEY,post_id TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE tasks(id TEXT PRIMARY KEY,state TEXT NOT NULL,body TEXT NOT NULL,updated TEXT NOT NULL);
        CREATE TABLE events(id INTEGER PRIMARY KEY,task_id TEXT,message TEXT NOT NULL,created TEXT NOT NULL);''')
    posts=[];snapshots=[]
    for i in range(size):
        pid=str(9000000000000000000000000+i)
        body=json.dumps({'id':pid,'text':('本地研究资料 中文与English 测试正文。'*25)+('目标标记' if i%10==0 else ''),'author':{'handle':'reader','name':'测试作者'},'relations':[],'media':[],'coverage':{'text_complete':True}},ensure_ascii=False)
        posts.append((pid,pid+'-2','','[]','2026-01-01',f'2026-01-01T{i:06}'))
        snapshots.extend((pid+'-'+str(v),pid,body,'2026-01-01') for v in range(3))
    db.executemany('INSERT INTO posts VALUES(?,?,?,?,?,?)',posts);db.executemany('INSERT INTO snapshots VALUES(?,?,?,?)',snapshots)
    for i in range(20):
        body={'topic':'合成研究','sources':[{'id':posts[j][0],'revision':posts[j][1]} for j in range(i*10,i*10+10)],'result':{'summary':'模型归纳'}}
        db.execute('INSERT INTO tasks VALUES(?,?,?,?)',(str(i),'completed',json.dumps(body,ensure_ascii=False),'2026-01-01'))
    db.commit()
    @contextlib.contextmanager
    def connection(self):
        with db: yield db
    def measure(fn):
        values=[]
        for _ in range(3):
            deadline=time.perf_counter()+5;db.set_progress_handler(lambda:time.perf_counter()>deadline,10000);start=time.perf_counter()
            try:fn()
            except sqlite3.OperationalError as error:
                if str(error)=='interrupted':return {'bounded_at_ms':5000}
                raise
            finally:db.set_progress_handler(None,0)
            values.append((time.perf_counter()-start)*1000)
        return round(statistics.median(values),2)
    before=None
    if module:
        Old=type('MemoryBaseline',(module.Store,),{'db':connection});old=Old.__new__(Old)
        # Public baselines with derived indexes need their normal initialization.
        if hasattr(old,'_index_post'): old=Old(ROOT)
        before={name:measure(fn) for name,fn in [('list',lambda:old.search(brief=True) if hasattr(old,'_index_post') else old.search()),('hit',lambda:old.search('目标标记',brief=True) if hasattr(old,'_index_post') else old.search('目标标记')),('miss',lambda:old.search('完全不存在的检索词',brief=True) if hasattr(old,'_index_post') else old.search('完全不存在的检索词'))]}
    Current=type('MemoryCurrent',(Store,),{'db':connection})
    current=Current(ROOT)
    after={name:measure(fn) for name,fn in [('list',lambda:current.search(brief=True)),('hit',lambda:current.search('目标标记',brief=True)),('miss',lambda:current.search('完全不存在的检索词',brief=True))]}
    result={'posts':size,'snapshots':size*3,'tasks':20,'sample':'synthetic_in_memory','metric':'median_ms_3_runs','baseline':baseline,'before':before,'after':after,'fts':current.fts}
    db.close();return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--posts',type=int,default=10000);parser.add_argument('--baseline',help='可选的公开标签或提交，如 v0.2.0');parser.add_argument('--output')
    args=parser.parse_args()
    if not 200<=args.posts<=30000:parser.error('--posts 应在200至30000之间')
    result=run(args.posts,args.baseline);text=json.dumps(result,ensure_ascii=False,indent=2);print(text)
    if args.output:
        path=Path(args.output).resolve()
        if not path.is_relative_to(ROOT):raise ValueError('输出必须位于项目内')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')

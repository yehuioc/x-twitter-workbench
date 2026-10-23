import json
import sys
import urllib.request
import urllib.error
from .store import HOME, Problem

TOOLS={
 'x_status':('status','查看连接与任务概况；完整来源、结果和事件通过 x_task 读取',{}),
 'x_search':('search','全文查找已保存原文、笔记和标签',{'query':{'type':'string'},'tag':{'type':'string'},'offset':{'type':'integer'}}),
 'x_local_sources':('local_sources','预览本地关键词匹配的完整来源和观察时间，不发起在线采集',{'topic':{'type':'string'},'keywords':{'type':'array','items':{'type':'string'}},'limit':{'type':'integer'},'threads':{'type':'boolean'}}),
 'x_get':('get','读取帖子及读取覆盖',{'id':{'type':'string'},'revision':{'type':'string'}}),
 'x_annotate':('annotate','更新个人笔记标签，不改写原文',{'id':{'type':'string'},'note':{'type':'string'},'tags':{'type':'array','items':{'type':'string'}}}),
 'x_browser':('browser','对用户允许的 X 绑定页执行固定动作；read_post/thread 自动保存，不开放互动或任意脚本',{'operation':{'enum':['identity','snapshot','search','open_post','read_post','read_thread','scroll']},'id':{'type':'string'},'keyword':{'type':'string'},'direction':{'enum':['up','down']},'limit':{'type':'integer','minimum':1,'maximum':10}}),
 'x_create_task':('create_task','创建研究；hybrid 先用本地匹配来源再补在线，online 强制在线，local 仅本地',{'topic':{'type':'string'},'keywords':{'type':'array','items':{'type':'string'}},'source_ids':{'type':'array','items':{'type':'string'}},'source_mode':{'enum':['hybrid','online','local']},'driver':{'enum':['external','cli','api']},'max_sources':{'type':'integer'},'max_scrolls':{'type':'integer'},'max_seconds':{'type':'integer'},'threads':{'type':'boolean'}}),
 'x_run':('run','启动有界研究任务',{'id':{'type':'string'}}),
 'x_cancel':('cancel','取消任务，拒绝接入迟到结果',{'id':{'type':'string'}}),
 'x_task':('task','查看研究进展和结果',{'id':{'type':'string'}}),
 'x_packet':('packet','取得固定原文版本与输出 schema，供当前客户端整理',{'id':{'type':'string'}}),
 'x_complete':('complete','按 schema 回填摘要、翻译、带精确引文的判断',{'id':{'type':'string'},'artifact':{'type':'object'}}),
 'x_source_image':('source_image','读取实际本地图片；index 从0开始',{'id':{'type':'string'},'revision':{'type':'string'},'index':{'type':'integer','minimum':0}}),
}
REQUIRED={'status':[],'search':[],'local_sources':[],'get':['id'],'annotate':['id','note','tags'],'browser':['operation'],'create_task':['topic'],'complete':['id','artifact'],'source_image':['id','index']}
def call(action,args,port):
    token=(HOME/'api-token.txt').read_text(encoding='utf-8')
    req=urllib.request.Request(f'http://127.0.0.1:{port}/api/v1',data=json.dumps({'action':action,'args':args}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=120) as r: return json.load(r)['result']
    except urllib.error.HTTPError as e: raise Problem(json.load(e).get('error','接口拒绝')) from e
def response(msg,port):
    if not isinstance(msg,dict) or msg.get('jsonrpc')!='2.0': return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}}
    if 'id' not in msg: return None
    base={'jsonrpc':'2.0','id':msg['id']}; method=msg.get('method')
    if method=='initialize': return base|{'result':{'protocolVersion':msg.get('params',{}).get('protocolVersion','2025-11-25'),'capabilities':{'tools':{}},'serverInfo':{'name':'x-workbench','version':'0.2.0'}}}
    if method=='ping': return base|{'result':{}}
    if method=='tools/list': return base|{'result':{'tools':[{'name':name,'description':desc,'inputSchema':{'type':'object','properties':props,'required':REQUIRED.get(action,['id']),'additionalProperties':False}} for name,(action,desc,props) in TOOLS.items()]}}
    if method=='tools/call':
        try:
            p=msg.get('params',{}); action,_,_=TOOLS[p['name']]; result=call(action,p.get('arguments',{}),port)
            return base|{'result':{'content':[result] if action=='source_image' else [{'type':'text','text':json.dumps(result,ensure_ascii=False)}],'isError':False}}
        except (Problem,OSError,ValueError,KeyError,TypeError) as e: return base|{'result':{'content':[{'type':'text','text':str(e)}],'isError':True}}
    return base|{'error':{'code':-32601,'message':'Method not found'}}
def main(port):
    for line in sys.stdin:
        try:
            if len(line)>2_000_000: raise ValueError('请求过大')
            value=response(json.loads(line),port)
        except ValueError: value={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        if value is not None: print(json.dumps(value,ensure_ascii=False),flush=True)

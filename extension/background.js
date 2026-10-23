const BASE='http://127.0.0.1:8915',panels=new Set();
const adapters=new Set(),mediaCache=new Map();let mediaCacheBytes=0;
let token='',binding=null,active=null,timer,ticking=false,initialized,message='请在工作台生成配对码。',connected=false,lastTick=0,writes=Promise.resolve();
const allowed=u=>{try{return ['x.com','twitter.com'].includes(new URL(u).hostname)&&new URL(u).protocol==='https:';}catch(_){return false;}};
const validId=id=>typeof id==='string'&&/^\d{1,25}$/.test(id);
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:true});
function state(){return {paired:!!token,binding,busy:!!active,message,connected,version:chrome.runtime.getManifest().version};}
function show(){for(const p of panels)try{p.postMessage(state());}catch(_){panels.delete(p);}}
function persist(){const value={binding,flight:active?{id:active.id,remote:active.remote,session:active.session}:null};writes=writes.catch(()=>{}).then(()=>chrome.storage.session.set({runner:value}));return writes;}
async function post(path,body){const r=await fetch(BASE+path,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+token},body:JSON.stringify(body),signal:AbortSignal.timeout(10000)});const data=await r.json();if(!r.ok)throw Error(data.error||'本机服务未连接');return data.result;}
async function init(){if(initialized)return initialized;initialized=(async()=>{token=(await chrome.storage.local.get('token')).token||'';const saved=(await chrome.storage.session.get('runner')).runner;if(saved?.binding){binding={...saved.binding,instance:saved.binding.instance||crypto.randomUUID(),version:chrome.runtime.getManifest().version};if(saved.flight)stop('上次动作中断，未重放；核对页面后重新开启');else if(binding.control)try{await check(binding.session);await identity();}catch(e){stop(e.message);}}schedule(0);return state();})();return initialized;}
function schedule(ms=binding?.control?1500:5000){clearTimeout(timer);if(binding&&token&&(binding.control||panels.size))timer=setTimeout(tick,ms);}
function stop(reason='AI 已停止，交还给你操作。'){
  if(active){active.cancelled=true;active.stopReason=reason;active.abort.abort();}
  if(binding){const old=binding;binding={...binding,control:false,grant_at:0,session:crypto.randomUUID(),revision:binding.revision+1};if(typeof old.autoDiscardable==='boolean')chrome.tabs.update(old.tab_id,{autoDiscardable:old.autoDiscardable}).catch(()=>{});}
  message=reason;chrome.alarms.clear('xwb-wake');persist();show();schedule(0);
}
async function check(session,controlled=false){const b=binding;if(!b||b.session!==session||active?.cancelled||controlled&&!b.control)throw Error('操作已停止或重新绑定');const t=await chrome.tabs.get(b.tab_id);if(binding?.session!==session)throw Error('绑定已变化');if(!allowed(t.url)||t.discarded||t.frozen){stop('页面已离开 X、冻结或被释放');throw Error(message);}binding.url=t.url;return t;}
function checkRun(run){if(run&&(active!==run||run.cancelled||run.abort.signal.aborted||Date.now()/1000>=run.expires))throw Error('操作已停止或过期');}
async function page(action,args={},run=active){
  checkRun(run);
  const session=binding?.session,t=await check(session);
  if(!adapters.has(t.id)){await chrome.scripting.executeScript({target:{tabId:t.id},world:'MAIN',files:['site.js']});adapters.add(t.id);}await check(session);
  const result=(await chrome.scripting.executeScript({target:{tabId:t.id},world:'MAIN',func:async(a,b)=>{try{return await xPageAction(a,b);}catch(error){return {adapter_error:String(error?.message||error)};}},args:[action,args]}))[0]?.result;
  await check(session);checkRun(run);if(result?.blocked){stop('平台提示：'+result.blocked);throw Error(message);}if(!result||typeof result!=='object')throw Error('页面未返回结构化结果，请等待页面加载后重试');if(result.adapter_error)throw Error(result.adapter_error);return result;
}
async function identity(){const r=await page('identity');if(!r.account||binding?.account&&r.account!==binding.account){stop('账号变化或无法核对，请重新绑定');throw Error(message);}return r;}
function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
async function until(probe,maximum=20000){const run=active,end=Math.min(Date.now()+maximum,active?.expires*1000||Infinity);while(Date.now()<end){checkRun(run);await check(run?.session||binding.session);const r=await probe();checkRun(run);if(r)return r;await sleep(350);}throw Error('页面未在时限内就绪；当前动作结束，未自动重放');}
async function navigate(url,id){await identity();await page('navigation_guard');const session=binding.session;await chrome.tabs.update(binding.tab_id,{url});await until(async()=>{const t=await check(session);if(t.status!=='complete'||id&&t.url.match(/\/status\/(\d+)/)?.[1]!==id)return false;await identity();return true;},25000);}
async function read(id){if(!validId(id))throw Error('帖子 ID 无效');let previous='';return until(async()=>{const r=await page('read_post',{id});if(!r?.ready)return false;const signature=JSON.stringify(r.posts.map(p=>[p.id,p.text,p.media,p.coverage.expanded]));if(signature!==previous){previous=signature;return false;}return r;},20000);}
async function imageData(posts){
  const run=active,items=posts.flatMap(p=>p.media||[]),pending=new Map();let index=0,downloaded=0,payload=0;
  async function load(raw){
    const url=new URL(raw);if(url.protocol!=='https:'||url.hostname!=='pbs.twimg.com')throw Error('图片域名未授权');
    const cached=mediaCache.get(raw);if(cached&&cached.expires>Date.now()){mediaCache.delete(raw);mediaCache.set(raw,cached);return cached;}
    if(cached){mediaCache.delete(raw);mediaCacheBytes-=cached.size;}
    const abort=new AbortController(),cancel=()=>abort.abort(),time=setTimeout(cancel,10000);run.abort.signal.addEventListener('abort',cancel,{once:true});
    try{
      const response=await fetch(raw,{credentials:'omit',redirect:'error',signal:abort.signal});if(!response.ok)throw Error('HTTP '+response.status);
      const reader=response.body.getReader(),chunks=[];let size=0;
      try{while(true){checkRun(run);const part=await reader.read();if(part.done)break;size+=part.value.length;downloaded+=part.value.length;if(size>8000000||downloaded>24000000)throw Error('图片超出保存大小');chunks.push(part.value);}}finally{await reader.cancel();}
      const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
      let binary='';for(let i=0;i<bytes.length;i+=16384)binary+=String.fromCharCode(...bytes.subarray(i,i+16384));
      const entry={data:btoa(binary),size,expires:Date.now()+300000};
      if(size<=6000000){while(mediaCacheBytes+size>6000000&&mediaCache.size){const key=mediaCache.keys().next().value;mediaCacheBytes-=mediaCache.get(key).size;mediaCache.delete(key);}mediaCache.set(raw,entry);mediaCacheBytes+=size;}
      return entry;
    }finally{clearTimeout(time);run.abort.signal.removeEventListener('abort',cancel);}
  }
  async function worker(){while(index<items.length){const media=items[index++];checkRun(run);await check(run.session);try{
    if(!pending.has(media.url))pending.set(media.url,load(media.url));
    const entry=await pending.get(media.url);checkRun(run);payload+=entry.size;if(payload>24000000)throw Error('图片超出本轮保存大小');
    media.data=entry.data;media.status='saved';delete media.error;
  }catch(error){checkRun(run);media.status='missing';media.error=error.message;delete media.data;}}}
  await Promise.all(Array.from({length:Math.min(2,items.length)},worker));return posts;
}
async function execute(action,args={}){
  const run=active;
  await identity();
  if(action==='identity')return page('identity');
  if(action==='snapshot')return page('snapshot');
  if(action==='scroll'){await page('scroll',args);await sleep(500);return page('snapshot');}
  if(action==='search'){if(typeof args.keyword!=='string'||!args.keyword.trim()||args.keyword.length>200)throw Error('查询无效');const u=new URL('https://x.com/search');u.searchParams.set('q',args.keyword);u.searchParams.set('f','live');await navigate(u.href);return until(async()=>{const r=await page('snapshot');const actual=new URL(r.url);return actual.pathname==='/search'&&actual.searchParams.get('q')===args.keyword&&(r.cards.length||r.empty)?r:false;});}
  if(!['open_post','read_post','read_thread'].includes(action)||!validId(args.id))throw Error('未开放此动作或帖子 ID 无效');
  const target=await check(run.session);if(target.url.match(/\/status\/(\d+)/)?.[1]!==args.id)await navigate('https://x.com/i/status/'+args.id,args.id);
  if(action==='open_post'){await read(args.id);return {opened:true,id:args.id};}
  const first=await read(args.id),posts=new Map(first.posts.map(p=>[p.id,p])),limit=Math.min(10,Math.max(1,Number(args.limit)||10));
  if(action==='read_thread'){
    const root=first.posts[0],same=p=>root.author.id&&p.author.id?root.author.id===p.author.id:!!root.author.handle&&p.author.handle===root.author.handle;
    const chain=new Set([args.id]);let visible=first.visible_posts,cursor=root;
    // A reply detail need not render its ancestors. Follow explicit parent IDs,
    // verify each author's identity, and stop at a different author or the budget.
    while(chain.size<limit){
      const parent=cursor.relations.find(r=>r.type==='reply_to')?.target;if(!parent||chain.has(parent))break;
      checkRun(run);await navigate('https://x.com/i/status/'+parent,parent);const detail=await read(parent),post=detail.posts.find(p=>p.id===parent);
      if(!post||!same(post))break;
      chain.add(parent);for(const p of detail.posts)posts.set(p.id,p);cursor=post;
    }
    if((await check(run.session)).url.match(/\/status\/(\d+)/)?.[1]!==args.id){await navigate('https://x.com/i/status/'+args.id,args.id);visible=(await read(args.id)).visible_posts;}
    for(let turn=0;turn<3&&chain.size<limit;turn++){
      let changed=true;while(changed&&chain.size<limit){changed=false;for(const p of visible){if(chain.has(p.id)||!same(p))continue;const explicit=p.relations.some(r=>r.type==='reply_to'&&chain.has(r.target))||[...posts.values()].some(s=>chain.has(s.id)&&s.relations.some(r=>r.type==='reply_to'&&r.target===p.id));if(explicit){posts.set(p.id,p);chain.add(p.id);changed=true;if(chain.size>=limit)break;}}}
      await page('scroll',{direction:'down'});await sleep(450);checkRun(run);visible=(await page('read_post',{id:args.id})).visible_posts||[];
    }
    for(const pid of chain){if(pid===args.id||posts.get(pid)?.coverage.text_complete===true)continue;checkRun(run);await navigate('https://x.com/i/status/'+pid,pid);const detail=await read(pid);for(const p of detail.posts)posts.set(p.id,p);}
    if((await check(run.session)).url.match(/\/status\/(\d+)/)?.[1]!==args.id){await navigate('https://x.com/i/status/'+args.id,args.id);}
    const ids=[],visited=new Set();function visit(id){if(visited.has(id))return;visited.add(id);const parent=posts.get(id)?.relations.find(r=>r.type==='reply_to')?.target;if(chain.has(parent))visit(parent);ids.push(id);}for(const id of chain)visit(id);
    for(const p of posts.values())p.coverage={...p.coverage,thread:chain.has(p.id)?'observed_partial':'quoted_context',thread_root:args.id,thread_ids:ids,thread_limit:limit,thread_reason:'只合并明确回复关系且同作者的已取得帖子；按预算追溯父帖并加载续帖，隐藏、删除或超出预算的部分仍可能缺失'};
  }
  return {posts:await imageData([...posts.values()]),coverage:{root_id:args.id,thread:action==='read_thread'?'observed_partial':'not_collected'}};
}
async function job(action,args,command,task){if(active)throw Error('页面正在操作，请等待或停止');const j={id:command?.id||crypto.randomUUID(),session:binding.session,remote:!!command,expires:command?.expires||Date.now()/1000+100,abort:new AbortController()};active=j;await persist();show();let result,error;
  try{result=await Promise.race([task?task():execute(action,args),new Promise((_,reject)=>{j.timeout=setTimeout(()=>reject(Error('动作超时，未重放')),Math.max(1,j.expires*1000-Date.now()));j.abort.signal.addEventListener('abort',()=>reject(Error(j.stopReason||'操作已停止')),{once:true});})]);await check(j.session);}
  catch(e){j.cancelled=true;j.abort.abort();error=e.message;message='当前动作未完成：'+error;}
  finally{clearTimeout(j.timeout);if(command)try{await post('/bridge/result',{id:j.id,session:j.session,result:error?null:result,error});}catch(e){message=e.message;}active=null;await persist();show();schedule(0);}
  if(error)throw Error(error);return result;
}
async function tick(){if(ticking)return;ticking=true;try{if(!binding||!token)return;if(binding.control&&lastTick&&Date.now()-lastTick>20000)stop('浏览器或电脑运行中断，请核对后重新开启');lastTick=Date.now();await check(binding.session);const sent=binding.session;const command=await post('/bridge/poll',{binding:{...binding},busy:!!active,executing:active?.remote?active.id:null});connected=true;if(sent!==binding.session)return;if(command?.stop)stop('本机工作台已停止旧授权，请主动重新开启');else if(command?.cancel&&active?.id===command.cancel)stop('当前任务已取消');else if(command?.id&&!active&&binding.control){if(command.session!==binding.session||Date.now()/1000>=command.expires)return;job(command.action,command.args,command).catch(()=>{});}}
  catch(e){connected=false;if(binding?.control)stop('本机连接中断，接管已暂停');else message='本机连接待确认：'+e.message;}finally{ticking=false;show();schedule();}}
async function capture(id,thread=false){
  if(!validId(id))throw Error('请先打开帖子或从列表选择');if(binding.control)throw Error('AI 正在接管，请先停止再手动保存');
  return job('manual_capture',{},null,async()=>{
  const original=await page('position');const result=await execute(thread?'read_thread':'read_post',{id});let saved;
  try{await post('/bridge/poll',{binding,busy:true});saved=await post('/bridge/capture',{...result,binding});}
  catch(e){const pending=(await chrome.storage.local.get('outbox')).outbox||[];const entry={account:binding.account,result,created:new Date().toISOString()};if(JSON.stringify([...pending,entry]).length>6000000)throw Error('本机保存失败且待同步空间不足；未声称保存成功，请启动服务后重试');await chrome.storage.local.set({outbox:[...pending,entry]});throw Error('本机保存未完成，已留在扩展待同步；恢复服务后点“同步待存记录”');}
  message='已保存原文与可取得图片，覆盖说明可在工作台查看。';
  try{const current=await check(binding.session);if(!original.url.match(/\/status\/(\d+)/)&&current.url.match(/\/status\/(\d+)/)?.[1]===id){await page('navigation_guard');await chrome.tabs.goBack(binding.tab_id);await until(async()=>{const t=await check(binding.session);return t.status==='complete'&&t.url===original.url;},15000);}await page('restore_scroll',{y:original.y});}catch(e){message+=' 返回阅读位置未完成：'+e.message;}
  show();return saved;
  });
}
async function handle(action,args={}){await init();if(action==='state')return state();if(action==='stop'){stop();if(token&&binding)await post('/bridge/poll',{binding,busy:!!active}).catch(()=>{});return state();}
  if(action==='pair'){if(active)throw Error('请先结束当前动作');stop();token=(await post('/bridge/pair',{code:String(args.code||'').trim().toUpperCase()})).token;await chrome.storage.local.set({token});message='已配对，请绑定当前 X 标签页。';show();return state();}
  if(action==='bind'){if(active)throw Error('请先结束当前动作');stop();const [t]=await chrome.tabs.query({active:true,currentWindow:true});if(!allowed(t?.url))throw Error('请选中登录后的 X 页面');binding={instance:binding?.instance||crypto.randomUUID(),tab_id:t.id,session:crypto.randomUUID(),revision:(binding?.revision||0)+1,account:'',control:false,grant_at:0,url:t.url,autoDiscardable:t.autoDiscardable,version:chrome.runtime.getManifest().version};try{binding.account=(await page('identity')).account;}catch(e){binding=null;await persist();throw e;}await chrome.scripting.executeScript({target:{tabId:t.id},files:['buttons.js']});await persist();message='绑定成功，可手动保存；AI 接管默认关闭。';schedule(0);show();return state();}
  if(!binding)throw Error('请先绑定 X 页面');
  if(action==='control'){if(!args.enabled)return handle('stop');if(active)throw Error('请等待当前动作结束');await identity();binding={...binding,control:true,grant_at:Date.now()/1000,session:crypto.randomUUID(),revision:binding.revision+1};lastTick=Date.now();await chrome.tabs.update(binding.tab_id,{autoDiscardable:false});await persist();await chrome.alarms.create('xwb-wake',{periodInMinutes:1});message='AI 接管开启；切页、最小化或关闭侧栏仍由后台执行。';schedule(0);show();return state();}
  if(action==='focus'){const t=await chrome.tabs.get(binding.tab_id);await chrome.tabs.update(t.id,{active:true});await chrome.windows.update(t.windowId,{focused:true});return state();}
  if(action==='list')return job('snapshot',{});
  if(action==='capture_post')return capture(args.id,args.thread===true);
  if(action==='capture'){const t=await check(binding.session),id=t.url.match(/\/status\/(\d+)/)?.[1];return capture(id,args.thread===true);}
  if(action==='sync'){await identity();await post('/bridge/poll',{binding,busy:false});const entries=(await chrome.storage.local.get('outbox')).outbox||[];let count=0;while(entries.length){if(entries[0].account!==binding.account)throw Error('待存记录属于另一账号，请先绑定对应账号');await post('/bridge/capture',{...entries[0].result,binding});entries.shift();count++;await chrome.storage.local.set({outbox:entries});}return {synced:count};}
  throw Error('未开放此操作');
}
chrome.runtime.onMessage.addListener((r,s,reply)=>{const panel=s.id===chrome.runtime.id&&s.url===chrome.runtime.getURL('panel.html');const content=s.id===chrome.runtime.id&&s.tab?.id===binding?.tab_id&&allowed(s.url)&&r.action==='capture_post';if(!panel&&!content)return false;handle(r.action,r.args).then(result=>reply({result}),error=>reply({error:error.message}));return true;});
chrome.runtime.onConnect.addListener(p=>{if(p.name!=='xwb-panel'||p.sender?.url!==chrome.runtime.getURL('panel.html'))return p.disconnect();panels.add(p);p.onDisconnect.addListener(()=>{panels.delete(p);schedule();});init().then(()=>{show();schedule(0);});});
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='xwb-wake')init().then(()=>schedule(0));});
chrome.runtime.onStartup.addListener(()=>init());chrome.runtime.onInstalled.addListener(()=>init());
chrome.tabs.onRemoved.addListener(id=>{adapters.delete(id);if(id===binding?.tab_id){stop('绑定标签页关闭，请重新绑定');binding=null;persist();show();}});
chrome.tabs.onUpdated.addListener((id,change,t)=>{if(change.status==='loading')adapters.delete(id);if(id!==binding?.tab_id)return;if(change.url&&!allowed(change.url))stop('绑定页已离开 X：'+change.url.slice(0,180));else if(change.discarded===true)stop('Chrome 报告绑定页已被释放（discarded），请检查页面后重新开启');else if(change.frozen===true)stop('Chrome 报告绑定页被冻结（frozen），请打开页面后重新开启');else if(change.status==='complete')chrome.scripting.executeScript({target:{tabId:id},files:['buttons.js']}).catch(()=>{});});

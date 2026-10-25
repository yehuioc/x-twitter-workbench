// Synthetic fixtures only: no live accounts, copied posts or captured media.
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const {xNormalizeTweet,xHandle,xDraftState,xBookmarkView}=require('../extension/site.js');
test('bookmarks use only the primary column, keep quotes separate, and reject other routes',()=>{
  const raw={rest_id:'9000000000000000000000001',legacy:{full_text:'收藏原文',truncated:false},quoted_status_result:{result:{rest_id:'9000000000000000000000002',legacy:{full_text:'引用上下文',truncated:false}}}};
  const article={__reactProps$test:{tweet:raw},querySelector:()=>null,querySelectorAll:s=>s==='time'?[{closest:()=>({href:'https://x.com/writer/status/'+raw.rest_id})}]:[]};
  const column={querySelector:()=>null,querySelectorAll:s=>s.startsWith('article')?[article]:[]};
  global.location={origin:'https://x.com',hostname:'x.com',pathname:'/i/bookmarks',href:'https://x.com/i/bookmarks'};global.scrollY=120;
  global.document={querySelector:s=>s.includes('primaryColumn')?column:s.includes('AppTabBar_Profile_Link')?{getAttribute:()=>'/reader'}:s.includes('AccountSwitcher')?{innerText:'@reader'}:null,querySelectorAll:()=>{throw Error('must not include sidebar recommendations');}};
  try{const r=xBookmarkView();assert.equal(r.account,'reader');assert.deepEqual(r.root_ids,[raw.rest_id]);assert.equal(r.posts.length,2);assert.equal(r.posts[0].text,'收藏原文');assert.equal(r.posts[1].text,'引用上下文');assert.equal(r.position.y,120);assert.equal(r.posts[0]._quote,undefined);global.location.pathname='/i/bookmarks/123';assert.throws(()=>xBookmarkView(),/全部书签/);global.location.pathname='/home';assert.throws(()=>xBookmarkView(),/全部书签/);}finally{delete global.location;delete global.scrollY;delete global.document;}
});
test('placeholder wrappers are not drafts; actual editor and textarea text still block navigation',()=>{
  const editor={tagName:'DIV',innerText:'\n',getAttribute:k=>k==='contenteditable'?'true':'tweetTextarea_0',matches:()=>true,querySelectorAll:()=>[]};
  const wrapper={tagName:'DIV',innerText:'发布你的回复',getAttribute:k=>k==='data-testid'?'tweetTextarea_0RichTextInputContainer':null,matches:()=>false,querySelectorAll:()=>[editor]};
  global.document={querySelectorAll:()=>[wrapper,editor]};
  try{assert.equal(xDraftState().has_draft,false);assert.equal(xDraftState().editable_fields,1);editor.innerText='尚未发送的真实文字';assert.equal(xDraftState().has_draft,true);editor.innerText='';const textarea={...editor,tagName:'TEXTAREA',value:'保存这段草稿'};global.document.querySelectorAll=()=>[textarea];assert.equal(xDraftState().has_draft,true);}finally{delete global.document;}
});
test('X identifiers, long text, quote and reply are separate',()=>{
  const raw={rest_id:'9000000000000000000000001',legacy:{full_text:'truncated…',truncated:true,in_reply_to_status_id_str:'9000000000000000000000002'},note_tweet:{note_tweet_results:{result:{text:'展开后的完整长帖。'}}},core:{user_results:{result:{rest_id:'123',legacy:{screen_name:'Author',name:'重复名字'}}}},quoted_status_result:{result:{rest_id:'99'}}};
  const p=xNormalizeTweet(raw);assert.equal(p.id,'9000000000000000000000001');assert.equal(p.text,'展开后的完整长帖。');assert.equal(p.author.handle,'author');assert.equal(p.coverage.text_complete,true);assert.deepEqual(p.relations.map(r=>r.type),['reply_to','quotes']);assert.equal(xHandle('same name'),'');
});
test('truncated field cannot claim full text; unavailable objects rejected',()=>{assert.equal(xNormalizeTweet({__typename:'TweetUnavailable'}),null);const p=xNormalizeTweet({rest_id:'1',legacy:{full_text:'excerpt',truncated:true}});assert.equal(p.coverage.text_complete,false);});
test('long-post resource links survive beyond truncated legacy entities',()=>{
  const p=xNormalizeTweet({rest_id:'1',legacy:{full_text:'短摘要',entities:{urls:[{expanded_url:'https://example.org/first'}]}},note_tweet:{note_tweet_results:{result:{text:'完整长帖',entity_set:{urls:[{expanded_url:'https://example.org/first'},{expanded_url:'https://github.com/example/project'}]}}}}});
  assert.deepEqual(p.links,['https://example.org/first','https://github.com/example/project']);
});
test('synthetic flat page fields preserve large author IDs, long text and quote identity',()=>{
  const p=xNormalizeTweet({id_str:'9000000000000000000000011',full_text:'原帖',user:{id_str:'9000000000000000000000099',screen_name:'Owner',name:'原作者'},note_tweet:{text:'真正展开后的长帖全文'},in_reply_to_status_id_str:'9000000000000000000000012',quoted_status:{id_str:'9000000000000000000000013',full_text:'被引用帖子'},entities:{media:[{type:'photo',media_url_https:'https://pbs.twimg.com/media/example.jpg'}]}});
  assert.equal(p.author.id,'9000000000000000000000099');assert.equal(p.text,'真正展开后的长帖全文');assert.equal(p.coverage.text_complete,true);assert.equal(p.relations[0].type,'reply_to');assert.equal(p.relations[1].target,'9000000000000000000000013');assert.equal(p.media.length,1);
});
test('save control replaces the legacy article child and stays inside the native action row',async()=>{
  class Element{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attrs={};this.dataset={};this.listeners={};this.namespaceURI='http://www.w3.org/2000/svg';}
    setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k]??null;}
    append(...items){for(const item of items){item.remove();this.children.push(item);item.parentElement=this;}}
    remove(){if(this.parentElement){const p=this.parentElement;p.children=p.children.filter(x=>x!==this);this.parentElement=null;}}
    replaceChildren(...items){for(const x of [...this.children])x.remove();this.append(...items);}
    contains(n){for(;n;n=n.parentElement)if(n===this)return true;return false;}
    matches(s){s=s.trim();if(s==='time')return this.tagName==='TIME';if(s==='a')return this.tagName==='A';if(s==='button[data-xwb-save]')return this.tagName==='BUTTON'&&'xwbSave'in this.dataset;const m=s.match(/^(\w+)?\[([\w-]+)=["']?([^"'\]]+)["']?\]$/);return !!m&&(!m[1]||this.tagName===m[1].toUpperCase())&&this.attrs[m[2]]===m[3];}
    querySelectorAll(s){const out=[];for(const c of this.children){if(s.split(',').some(x=>c.matches(x)))out.push(c);out.push(...c.querySelectorAll(s));}return out;}
    querySelector(s){return this.querySelectorAll(s)[0]||null;}closest(s){for(let n=this;n;n=n.parentElement)if(n.matches(s))return n;return null;}
    addEventListener(name,fn){this.listeners[name]=fn;}
  }
  const root=new Element('html'),article=new Element('article'),time=new Element('time'),link=new Element('a'),row=new Element('div'),reply=new Element('button'),legacy=new Element('button');article.setAttribute('data-testid','tweet');reply.setAttribute('data-testid','reply');row.setAttribute('role','group');link.href='https://x.com/Owner/status/9000000000000000000000011';legacy.dataset.xwbSave='';link.append(time);row.append(reply);article.append(link,row,legacy);root.append(article);
  const document={documentElement:root,querySelectorAll:s=>root.querySelectorAll(s),getElementById:id=>root.children.find(x=>x.id===id),createElement:t=>new Element(t),createElementNS:(ns,t)=>new Element(t)};let request,error=false;
  const context={document,window:{__xwbButtons:true},MutationObserver:class{observe(){}disconnect(){}},setTimeout,clearTimeout,chrome:{runtime:{async sendMessage(r){request=r;return error?{error:'当前动作失败，错误不应撑大按钮'}:{result:{saved:[{id:'9000000000000000000000011'}]}};}}}};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../extension/buttons.js'),'utf8'),context);
  const button=article.querySelector('button[data-xwb-save]');assert.ok(button);assert.equal(legacy.parentElement,null);assert.equal(button.parentElement.parentElement,row);assert.equal(article.querySelectorAll('button[data-xwb-save]').length,1);
  await button.listeners.click({preventDefault(){},stopPropagation(){}});assert.equal(request.args.id,'9000000000000000000000011');assert.equal(button.dataset.state,'saved');assert.equal(button.children[0].tagName,'SVG');
  error=true;await button.listeners.click({preventDefault(){},stopPropagation(){}});assert.equal(button.dataset.state,'error');assert.match(button.title,/当前动作失败/);assert.equal(button.children[0].tagName,'SVG');
});
function runner(){
  const listeners={},local={},session={},timers=new Set(),actions=[],injections=[],navigations=[],posted=[];let bookmarkResult=null,mediaResponse=null;let tab={id:7,url:'https://x.com/home',status:'complete',autoDiscardable:true},account='reader',failRead=false,postMap={},nullAction='';
  const event=name=>({addListener(fn){listeners[name]=fn;}});
  const storage=obj=>({async get(key){if(Array.isArray(key))return Object.fromEntries(key.map(k=>[k,obj[k]]));return {[key]:obj[key]};},async set(value){Object.assign(obj,JSON.parse(JSON.stringify(value)));}});
  const chrome={sidePanel:{setPanelBehavior(){}},storage:{local:storage(local),session:storage(session)},alarms:{async clear(){},async create(){},onAlarm:event('alarm')},runtime:{getManifest:()=>({version:'0.1.0'}),getURL:p=>'chrome-extension://'+'a'.repeat(32)+'/'+p,onMessage:event('message'),onConnect:event('connect'),onStartup:event('startup'),onInstalled:event('installed')},windows:{async update(){}},tabs:{async query(){return [tab];},async get(){return {...tab};},async update(id,patch){if(patch.url)navigations.push(patch.url);Object.assign(tab,patch);return {...tab};},async goBack(){tab.url='https://x.com/home';},onRemoved:event('removed'),onUpdated:event('updated')},scripting:{async executeScript(req){if(req.files){injections.push(...req.files);return [{result:true}];}const a=req.args?.[0],args=req.args?.[1];actions.push(a);if(a===nullAction)return [{result:null}];if(a==='identity')return [{result:{account}}];if(a==='navigation_guard')return [{result:{safe:true}}];if(a==='position')return [{result:{url:tab.url,y:0}}];if(a==='bookmarks')return [{result:bookmarkResult?{...bookmarkResult,account,url:tab.url}:{account,url:tab.url,root_ids:[],posts:[],empty:true}}];if(a==='read_post'){if(failRead)throw Error('recoverable selector failure');return [{result:{ready:true,posts:[postMap[args.id]||{id:args.id,text:'正文',author:{handle:'reader'},relations:[],media:[],coverage:{text_complete:true}}],visible_posts:[]}}];}return [{result:{cards:[],url:tab.url}}];}}};
  const sandbox={chrome,URL,AbortController,AbortSignal,crypto:require('node:crypto').webcrypto,fetch:async(url,options)=>{if(mediaResponse&&url.startsWith('https://pbs.twimg.com/'))return mediaResponse(url,options);const body=JSON.parse(options.body);posted.push({url,body});return {ok:true,json:async()=>({result:url.endsWith('/pair')?{token:'paired'}:url.endsWith('/capture')?{saved:body.posts,bookmark_ids:body.root_ids}:null})};},setTimeout:(fn,ms)=>{const t=setTimeout(fn,ms);timers.add(t);return t;},clearTimeout:t=>{clearTimeout(t);timers.delete(t);},console,btoa:s=>Buffer.from(s,'binary').toString('base64')};
  vm.createContext(sandbox);vm.runInContext(fs.readFileSync(path.join(__dirname,'../extension/background.js'),'utf8')+'\nglobalThis.api={handle,state,execute,job,stop,checkRun};',sandbox);
  return {api:sandbox.api,session,local,listeners,tab,actions,injections,navigations,posted,bookmarks(value){bookmarkResult=value;},media(fn){mediaResponse=fn;},fail(){failRead=true;},posts(data){postMap=data;},nullResult(action){nullAction=action;},account(a){account=a;},close(){for(const t of timers)clearTimeout(t);}};
}
test('background owns binding; recoverable error keeps grant; account drift stops',async()=>{
  const r=runner();try{await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');assert.equal(r.api.state().binding.control,false);await r.api.handle('control',{enabled:true});assert.equal(r.api.state().binding.control,true);await r.api.job('open_post',{id:'123'});r.fail();await assert.rejects(r.api.job('read_post',{id:'123'}),/selector failure/);assert.equal(r.api.state().binding.control,true);r.account('someoneelse');await assert.rejects(r.api.job('identity',{}),/账号/);assert.equal(r.api.state().binding.control,false);}finally{r.close();}
});
test('stop rotates grant; future old run cannot act; interaction and unknown actions refused',async()=>{
  const r=runner();try{await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');await r.api.handle('control',{enabled:true});const old=r.api.state().binding.session;await r.api.handle('stop');assert.notEqual(r.api.state().binding.session,old);await assert.rejects(r.api.job('publish',{}),/未开放/);await assert.rejects(r.api.job('read_post',{id:123}),/ID/);assert.throws(()=>r.api.checkRun({cancelled:true,abort:{signal:{aborted:false}},expires:Infinity}),/停止/);}finally{r.close();}
});
test('thread reading follows missing same-author ancestors and orders parents before replies',async()=>{
  const r=runner();try{
    await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');await r.api.handle('control',{enabled:true});
    const make=(id,parent)=>({id,text:'真实完整正文 '+id,author:{id:'42',handle:'writer'},relations:parent?[{type:'reply_to',target:parent}]:[],media:[],coverage:{text_complete:true}});
    r.posts({'300':make('300','200'),'200':make('200','100'),'100':make('100')});r.tab.url='https://x.com/writer/status/300';
    const result=await r.api.job('read_thread',{id:'300',limit:3});assert.deepEqual(Array.from(result.posts[0].coverage.thread_ids),['100','200','300']);assert.equal(result.posts.length,3);assert.match(r.tab.url,/status\/300$/);assert.equal(r.api.state().binding.control,true);
  }finally{r.close();}
});
test('null page result is an explicit recoverable failure, not a success or TypeError',async()=>{
  const r=runner();try{await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');await r.api.handle('control',{enabled:true});r.nullResult('navigation_guard');await assert.rejects(r.api.job('open_post',{id:'123'}),/未返回结构化结果/);assert.equal(r.tab.url,'https://x.com/home');assert.equal(r.api.state().binding.control,true);}finally{r.close();}
});
test('read action navigates once and injects the adapter once per document',async()=>{
  const r=runner();try{
    await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');await r.api.handle('control',{enabled:true});
    await r.api.job('read_post',{id:'123'});assert.equal(r.navigations.length,1);assert.equal(r.actions.filter(a=>a==='read_post').length,2);assert.equal(r.injections.filter(f=>f==='site.js').length,1);
    r.listeners.updated(7,{status:'loading'},r.tab);await r.api.job('read_post',{id:'123'});assert.equal(r.navigations.length,1);assert.equal(r.injections.filter(f=>f==='site.js').length,2);
  }finally{r.close();}
});
test('media uses bounded concurrency, deduplicates URLs and reuses the short cache',async()=>{
  const r=runner();try{
    await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');await r.api.handle('control',{enabled:true});
    const urls=['a','b','a','c'].map(x=>'https://pbs.twimg.com/media/'+x+'.png');let requests=0,inflight=0,peak=0;
    r.posts({'123':{id:'123',text:'图片帖子',author:{handle:'writer'},relations:[],media:urls.map(url=>({url})),coverage:{text_complete:true}}});
    r.media(async()=>{requests++;inflight++;peak=Math.max(peak,inflight);let done=false;return {ok:true,body:{getReader:()=>({async read(){if(done)return {done:true};await new Promise(resolve=>setTimeout(resolve,15));done=true;inflight--;return {done:false,value:new Uint8Array([137,80,78,71])};},async cancel(){}})}};});
    const first=await r.api.job('read_post',{id:'123'});assert.equal(requests,3);assert.ok(peak<=2&&peak>1);assert.ok(first.posts[0].media.every(m=>m.status==='saved'&&m.data));
    await r.api.job('read_post',{id:'123'});assert.equal(requests,3);
  }finally{r.close();}
});

test('manual bookmark capture works without AI grant and posts only verified root membership',async()=>{
  const r=runner();try{
    await r.api.handle('pair',{code:'CODE'});await r.api.handle('bind');
    r.bookmarks({root_ids:['123'],posts:[{id:'123',text:'收藏原文',media:[],coverage:{text_complete:true}}],empty:false});
    const saved=await r.api.handle('bookmarks');assert.deepEqual(Array.from(saved.bookmark_ids),['123']);assert.equal(r.api.state().binding.control,false);assert.equal(r.navigations.at(-1),'https://x.com/i/bookmarks');
    const capture=r.posted.find(x=>x.url.endsWith('/capture'));assert.ok(capture);assert.equal(capture.body.account,'reader');assert.deepEqual(Array.from(capture.body.root_ids),['123']);
    await r.api.handle('control',{enabled:true});await assert.rejects(r.api.handle('bookmarks'),/手动保存前先停止/);
  }finally{r.close();}
});

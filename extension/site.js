/* Fixed X page adapter. No arbitrary script, selectors, cookie or private-message access. */
function xStatusId(url) {try {const u=new URL(url,location.origin);return ['x.com','twitter.com'].includes(u.hostname)?u.pathname.match(/\/status\/(\d+)(?:\/|$)/)?.[1]||'':'';}catch(_){return '';}}
function xHandle(value) {return String(value||'').match(/^@?([A-Za-z0-9_]{1,15})$/)?.[1]?.toLowerCase()||'';}
function xNormalizeTweet(t) {
  t=t?.tweet||t;const l=t?.legacy||t;if(!l||!/^\d+$/.test(String(t.rest_id||l.id_str||'')))return null;
  const user=t.core?.user_results?.result||t.user, ul=user?.legacy||user||{}, uc=user?.core||{};
  const handle=xHandle(ul.screen_name||uc.screen_name), note=t.note_tweet?.note_tweet_results?.result||t.note_tweet;
  const full=note?.text||l.full_text||'', relations=[];
  if(l.in_reply_to_status_id_str)relations.push({type:'reply_to',target:String(l.in_reply_to_status_id_str),evidence:'page_post_fields'});
  const quote=t.quoted_status_result?.result||t.quoted_status, qid=quote?.rest_id||quote?.id_str||l.quoted_status_id_str;
  if(qid)relations.push({type:'quotes',target:String(qid),evidence:'page_post_fields'});
  const links=[...new Set([...(l.entities?.urls||[]),...((note?.entity_set||note?.entities)?.urls||[])].map(x=>x.expanded_url||x.url).filter(Boolean))];
  return {id:String(t.rest_id||l.id_str),text:full,author:{id:String(user?.rest_id||user?.id_str||l.user_id_str||''),handle,name:ul.name||uc.name||''},
    published_at:l.created_at||null,lang:l.lang||null,relations,links,
    media:(l.extended_entities?.media||l.entities?.media||[]).filter(x=>x.type==='photo').map(x=>({url:x.media_url_https,alt:x.ext_alt_text||''})),
    coverage:{text_complete:!!note?.text||l.truncated===false&&!t.note_tweet,method:'page_post_fields',thread:'not_collected',video:'链接与封面以外未保存'},
    metrics:{likes:l.favorite_count,replies:l.reply_count,reposts:l.retweet_count},extraction:{structured:true,note_tweet:!!note?.text},_quote:quote};
}
function xStructured(article,id) {
  const queue=[],seen=new Set(),allowed=['children','props','tweet','tweetResult','result','tweet_results','item','content','itemContent','memoizedProps','data','value','itemData','tweetData'];
  for(let node=article,i=0;node&&i<5;node=node.parentElement,i++)for(const key of Object.keys(node)){
    if(key.startsWith('__reactProps$'))queue.push([node[key],0]);
    if(key.startsWith('__reactFiber$')){
      for(let owner=node[key],i=0;owner&&i<24;owner=owner.return,i++){
        const post=xNormalizeTweet(owner.memoizedProps?.tweet);if(post?.id===id)return post;
      }
      queue.push([node[key]?.memoizedProps,0]);
    }
  }
  for(let i=0;i<queue.length&&i<5000;i++){
    const [obj,depth]=queue[i];if(!obj||typeof obj!=='object'||seen.has(obj)||depth>50)continue;seen.add(obj);
    const p=xNormalizeTweet(obj);if(p?.id===id)return p;
    if(Array.isArray(obj))for(const child of obj.slice(0,50))queue.push([child,depth+1]);
    else for(const key of allowed)if(obj[key]&&typeof obj[key]==='object')queue.push([obj[key],depth+1]);
  }return null;
}
function xArticleId(article) {
  return [...article.querySelectorAll('time')].map(t=>xStatusId(t.closest('a')?.href)).find(Boolean)||'';
}
function xInQuote(node,article,id) {
  for(let n=node;n&&n!==article;n=n.parentElement){
    if(n.getAttribute?.('data-testid')==='quoteTweet')return true;
    if(n.getAttribute?.('role')==='link'){
      const ids=[...n.querySelectorAll('a[href]')].map(a=>xStatusId(a.href)).filter(Boolean);
      if(ids.some(x=>x!==id)&&!ids.includes(id))return true;
    }
  }return false;
}
function xReadArticle(article) {
  const id=xArticleId(article);if(!id)return null;
  const structured=xStructured(article,id), text=[...article.querySelectorAll('[data-testid="tweetText"]')].filter(n=>!xInQuote(n,article,id))[0]?.innerText?.trim()||'';
  const show=article.querySelector('[data-testid="tweet-text-show-more-link"]');
  const ownLinks=[...article.querySelectorAll('a[href]')], authorLink=article.querySelector('[data-testid="User-Name"] a[href]');
  const handle=xHandle(authorLink?.getAttribute('href')?.split('/').filter(Boolean)[0]);
  const base=structured||{id,text,author:{id:'',handle,name:article.querySelector('[data-testid="User-Name"]')?.innerText?.split('\n')[0]||''},published_at:article.querySelector('time')?.dateTime||null,relations:[],links:ownLinks.map(a=>a.href).filter(u=>!/^https:\/\/(x|twitter)\.com\//.test(u)),metrics:{},media:[],extraction:{structured:false},coverage:{method:'rendered_detail',thread:'not_collected'}};
  if(!structured||!structured.coverage.text_complete) {
    if(text.length>base.text.length)base.text=text;
    base.coverage.text_complete=xStatusId(location.href)===id&&!show&&!!text&&!structured?.extraction?.note_tweet;
    if(structured&&structured.extraction.note_tweet)base.coverage.text_complete=true;
  }
  if(!base.media.length)base.media=[...article.querySelectorAll('[data-testid="tweetPhoto"] img')].filter(img=>!xInQuote(img,article,id)).map(img=>({url:img.currentSrc||img.src,alt:img.alt||''})).filter(x=>{try{return new URL(x.url).hostname==='pbs.twimg.com';}catch(_){return false;}});
  base.coverage={...base.coverage,expanded:!show,images_reported:base.media.length,article:ownLinks.some(a=>/\/i\/article\//.test(a.href))?'linked_article_not_read':'not_detected',observed_only:true};
  base.url='https://x.com/i/status/'+id;base.observed_at=new Date().toISOString();return base;
}
function xPageIdentity() {
  const link=document.querySelector('a[data-testid="AppTabBar_Profile_Link"]');
  const handle=xHandle(link?.getAttribute('href')?.split('/').filter(Boolean)[0]);
  const switcher=document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]')?.innerText||'';
  const shown=[...switcher.matchAll(/@([A-Za-z0-9_]{1,15})/g)].map(x=>xHandle(x[1]));
  if(!handle)return null;
  if(shown.some(x=>x!==handle))return {ready:true,account:'',conflict:true,reason:'账号入口与切换按钮不一致'};
  return {ready:true,account:handle,verification:'self_profile_link_and_account_switcher',url:location.href};
}
function xWaitIdentity(){
  const current=xPageIdentity();if(current)return Promise.resolve(current);
  return new Promise(resolve=>{
    let observer,timer;const finish=value=>{observer?.disconnect();clearTimeout(timer);resolve(value);};
    const inspect=()=>{const value=xPageIdentity();if(value)finish(value);};
    observer=new MutationObserver(inspect);observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true});
    timer=setTimeout(()=>finish({ready:false,account:'',reason:'页面账号入口尚未就绪'}),12000);inspect();
  });
}
function xDraftState(){
  // X's tweetTextarea prefix also matches containers holding the placeholder.
  // Only editable fields contain draft content; wrapper labels are not drafts.
  const candidates=[...document.querySelectorAll('[data-testid^="tweetTextarea"], [data-testid="dmComposerTextInput"], textarea')];
  const fields=new Set();
  for(const element of candidates){
    if(element.matches('textarea, input, [contenteditable="true"]'))fields.add(element);
    for(const field of element.querySelectorAll('[contenteditable="true"], textarea, input'))fields.add(field);
  }
  return {has_draft:[...fields].some(e=>(e.value||e.innerText||'').trim()),diagnostics:candidates.map(e=>({test_id:e.getAttribute('data-testid'),tag:e.tagName,editable:e.getAttribute('contenteditable'),text_length:(e.innerText||'').trim().length,value_length:(e.value||'').trim().length})),editable_fields:fields.size};
}
function xBookmarkView(){
  if(!/^\/i\/bookmarks\/?$/.test(location.pathname))throw Error('请打开全部书签页；文件夹、搜索和推荐内容不能当成全部书签');
  const column=document.querySelector('[data-testid="primaryColumn"]')||document.querySelector('main');
  const roots=[...(column?.querySelectorAll('article[data-testid="tweet"]')||[])].map(xReadArticle).filter(Boolean),posts=new Map();
  for(const post of roots){
    const quoted=post._quote&&xNormalizeTweet(post._quote);delete post._quote;posts.set(post.id,post);
    if(quoted){delete quoted._quote;quoted.observed_at=post.observed_at;quoted.url='https://x.com/i/status/'+quoted.id;posts.set(quoted.id,quoted);}
  }
  return {adapter_version:'0.3.0',account:xPageIdentity()?.account||'',url:location.href,root_ids:roots.map(p=>p.id),posts:[...posts.values()],empty:!roots.length&&!!column?.querySelector('[data-testid="emptyState"]'),loading:!!column?.querySelector('[role="progressbar"]'),unavailable:column?.querySelectorAll('[data-testid="tombstone"]').length||0,position:{y:scrollY},scope:'当前全部书签页已加载部分；不代表全部历史书签'};
}
async function xPageAction(action,args={}) {
  if(!['x.com','twitter.com'].includes(location.hostname))throw Error('绑定页已离开 X');
  let dialogs=[...document.querySelectorAll('[role="dialog"],[data-testid="error-detail"],[data-testid="toast"]')].map(x=>x.innerText||'').join('\n');
  if(!document.querySelector('article[data-testid="tweet"]'))dialogs+='\n'+(document.querySelector('main')?.innerText||'');
  if(/captcha|verify your identity|unusual activity|account.*locked|验证码|验证你的身份|账号.*锁定|操作频繁|rate limit|log in to x|sign in to x|登录 X/i.test(dialogs))return {blocked:dialogs.slice(0,400)};
  if(/\/(i\/flow\/login|account\/access)/.test(location.pathname))return {blocked:'X 要求登录或验证'};
  if(action==='identity'){
    return xWaitIdentity();
  }
  if(action==='navigation_guard') {
    if(xDraftState().has_draft)throw Error('页面有未发送文字，请先自行保存或处理；不会导航覆盖');
    return {safe:true};
  }
  if(action==='position')return {url:location.href,y:scrollY};
  if(action==='restore_scroll'){window.scrollTo({top:Math.max(0,Math.min(10000000,Number(args.y)||0)),behavior:'instant'});return {restored:true};}
  if(action==='bookmarks')return xBookmarkView();
  const articles=[...document.querySelectorAll('article[data-testid="tweet"]')];
  if(action==='snapshot')return {adapter_version:'0.2.0',navigation_guard:xDraftState(),button_style:!!document.getElementById('xwb-action-style'),button_runtime:{version:document.documentElement.dataset.xwbUiVersion,error:document.documentElement.dataset.xwbUiError},cards:articles.map(xReadArticle).filter(Boolean).map(p=>({id:p.id,text:p.text.slice(0,240),author:p.author,url:p.url})),url:location.href,empty:!articles.length&&!!document.querySelector('[data-testid="emptyState"]'),scope:'当前已加载列表，尚未精读',save_buttons:articles.slice(0,10).map(a=>{const b=a.querySelector('button[data-xwb-save]'),r=b?.closest('[role="group"]');return {id:xArticleId(a),present:!!b,in_action_row:!!r&&!!r.querySelector('[data-testid="reply"]'),width:b?.getBoundingClientRect().width,height:b?.getBoundingClientRect().height,version:b?.dataset.xwbSave};})};
  if(action==='scroll'){window.scrollBy({top:(args.direction==='up'?-1:1)*Math.min(innerHeight*.8,850),behavior:'instant'});return {scrolled:true};}
  if(action==='read_post'){
    if(xStatusId(location.href)!==args.id)throw Error('当前地址与目标帖子不同，拒绝读取旧列表');
    const article=articles.find(a=>xArticleId(a)===args.id);if(!article)return {ready:false};
    const more=article.querySelector('[data-testid="tweet-text-show-more-link"]');
    if(more){more.click();return {ready:false,expanding:true};}
    const post=xReadArticle(article);if(!post||!post.text&&!post.media.length)return {ready:false};
    const posts=[post];if(post._quote){const quoted=xNormalizeTweet(post._quote);if(quoted){quoted.observed_at=post.observed_at;quoted.url='https://x.com/i/status/'+quoted.id;quoted.coverage.images_reported=quoted.media.length;delete quoted._quote;posts.push(quoted);}}
    delete post._quote;return {ready:true,posts,visible_posts:articles.map(xReadArticle).filter(Boolean).map(p=>{delete p._quote;return p;})};
  }
  throw Error('此页面动作未开放');
}
if(typeof module!=='undefined')module.exports={xNormalizeTweet,xHandle,xDraftState,xBookmarkView};

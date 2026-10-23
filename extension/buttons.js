/* Manual save belongs to the native action row, never to the article flex root. */
(() => {
  const VERSION='0.2.0';
  if(window.__xwbButtons?.version===VERSION){window.__xwbButtons.update();return;}
  window.__xwbButtons?.observer?.disconnect();
  for(const wrapper of document.querySelectorAll('[data-xwb-action]'))wrapper.remove();
  for(const button of document.querySelectorAll('button[data-xwb-save]'))button.remove();
  if(!document.getElementById('xwb-action-style')){
    const style=document.createElement('style');style.id='xwb-action-style';
    style.textContent=`
      [data-xwb-action]{display:flex;align-items:center;justify-content:center;flex:0 0 30px;min-width:30px;align-self:center}
      button[data-xwb-save]{appearance:none;display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;min-width:30px;min-height:30px;margin:0;padding:5px;border:0;border-radius:50%;background:transparent;color:rgb(83,100,113);cursor:pointer;line-height:1;flex-shrink:0;vertical-align:middle}
      button[data-xwb-save]:hover{background:rgba(29,155,240,.1);color:rgb(29,155,240)}
      button[data-xwb-save]:focus-visible{outline:2px solid rgb(29,155,240);outline-offset:2px}
      button[data-xwb-save][data-state=saved]{color:rgb(0,145,110)}
      button[data-xwb-save][data-state=error]{color:rgb(205,65,85)}
      button[data-xwb-save]:disabled{cursor:progress;opacity:.65}
      button[data-xwb-save] svg{display:block;width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;pointer-events:none}
      button[data-xwb-save][data-state=saving] svg{animation:xwb-saving 1s linear infinite}
      @keyframes xwb-saving{to{transform:rotate(360deg)}}
      @media(prefers-reduced-motion:reduce){button[data-xwb-save][data-state=saving] svg{animation:none}}
    `;document.documentElement.append(style);
  }
  function icon(button,state){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d',state==='saved'?'M5 12l4 4L19 6':state==='saving'?'M20 12a8 8 0 1 1-8-8':state==='error'?'M12 4 2.5 20h19L12 4Zm0 5v5m0 3h.01':'M3.5 5h6l2 2h9v12h-17V5Zm9 5v6m-3-3h6');svg.append(path);button.replaceChildren(svg);button.dataset.state=state;
  }
  function update(articles=document.querySelectorAll('article[data-testid="tweet"]')){try{
    document.documentElement.dataset.xwbUiVersion=VERSION;
    for(const article of articles){
      if(article.isConnected===false)continue;
      const id=article.querySelector('time')?.closest('a')?.href.match(/\/status\/(\d+)/)?.[1];if(!id)continue;
      const control=article.querySelector('[data-testid="reply"],[data-testid="retweet"],[data-testid="like"],[data-testid="unlike"]');
      const row=control?.closest('[role="group"]');if(!row||!article.contains(row))continue;
      let button=article.querySelector('button[data-xwb-save]');
      if(!button){
        const wrapper=document.createElement('div');wrapper.dataset.xwbAction='';
        button=document.createElement('button');button.type='button';button.dataset.xwbSave=VERSION;button.title='保存到本地工作台';button.setAttribute('aria-label','保存到本地工作台');icon(button,'idle');wrapper.append(button);row.append(wrapper);
        button.addEventListener('click',async event=>{
          event.preventDefault();event.stopPropagation();button.disabled=true;icon(button,'saving');button.title='正在保存到本地工作台';
          try{const result=await chrome.runtime.sendMessage({action:'capture_post',args:{id:button.dataset.id}});if(result?.error||!Array.isArray(result?.result?.saved)||!result.result.saved.length)throw Error(result?.error||'扩展后台未返回保存确认');icon(button,'saved');button.title='已保存到本地工作台；再次点击可更新';button.setAttribute('aria-label','已保存到本地工作台');}
          catch(error){icon(button,'error');button.title='保存未完成：'+error.message;button.setAttribute('aria-label',button.title);}
          finally{button.disabled=false;}
        });
      }
      if(button.dataset.id!==id){button.dataset.id=id;icon(button,'idle');button.title='保存到本地工作台';button.setAttribute('aria-label','保存到本地工作台');}
      if(button.parentElement?.parentElement!==row)row.append(button.parentElement);
    }
  }catch(error){document.documentElement.dataset.xwbUiError=error.message;}}
  let timer;const dirty=new Set(),observer=new MutationObserver(records=>{
    for(const record of records){
      if(record.target.closest?.('[data-xwb-action]'))continue;
      const article=record.target.closest?.('article[data-testid="tweet"]');if(article)dirty.add(article);
      for(const node of record.addedNodes){if(node.matches?.('article[data-testid="tweet"]'))dirty.add(node);for(const a of node.querySelectorAll?.('article[data-testid="tweet"]')||[])dirty.add(a);}
    }
    if(dirty.size&&!timer)timer=setTimeout(()=>{timer=null;const batch=[...dirty];dirty.clear();update(batch);},150);
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});window.__xwbButtons={version:VERSION,observer,update};update();
})();

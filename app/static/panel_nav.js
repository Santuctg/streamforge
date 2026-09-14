/* STREAMFORGE_MAIN_PANEL_STATIC_SHELL_ASSETS_V1116: cacheable hidden-route/native-navigation runtime. */
(() => {
  /* STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49:
     Keep normal full-document reload feedback while the configured Panel/API
     root remains the only browser-visible URL. Menu links store a validated
     internal GET route in a short-lived cookie and reload the canonical root;
     Main middleware dispatches that root request to the selected panel route. */
  const root=String(window.STREAMFORGE_APP_ROOT||'').replace(/\/+$/,'');
  const canonical=root||'/';
  // STREAMFORGE_MAIN_PANEL_HIDE_HOVER_RUNTIME_V99R18:
  const hideHover=Boolean(window.STREAMFORGE_HIDE_PANEL_HOVER_URLS);
  const cookieName='streamforge_panel_route';
  const panelPrefixes=[
    '/channels','/nodes','/categories','/users','/playlists','/logs',
    '/admin-users','/roles','/system','/viewer-sessions','/account','/login','/logout','/no-access'
  ];
  const splitPath=(value)=>{
    try{
      const url=new URL(String(value||''),location.origin);
      if(url.origin!==location.origin) return null;
      let path=url.pathname||'/';
      if(root&&(path===root||path.startsWith(root+'/'))) path=path.slice(root.length)||'/';
      return {path,search:url.search||'',hash:url.hash||''};
    }catch(_){return null;}
  };
  const isPanelPath=(value)=>{
    const parsed=splitPath(value);
    if(!parsed) return false;
    const path=parsed.path;
    return path==='/'||panelPrefixes.some(prefix=>path===prefix||path.startsWith(prefix+'/'));
  };
  const internalValue=(value)=>{
    const parsed=splitPath(value);
    if(!parsed||!isPanelPath(value)) return '';
    return parsed.path+parsed.search+parsed.hash;
  };
  const addRoot=(value)=>{
    if(typeof value!=='string'||!value.startsWith('/')) return value;
    const parsed=splitPath(value);
    if(!parsed||!isPanelPath(value)) return value;
    if(root&&(value===root||value.startsWith(root+'/'))) return value;
    return parsed.path==='/' ? (root||'/') : root+parsed.path+parsed.search+parsed.hash;
  };
  const writeCookie=(value)=>{
    try{document.cookie=cookieName+'='+encodeURIComponent(value)+'; Path=/; Max-Age=43200; SameSite=Lax';}catch(_){}
  };
  let internalState=internalValue(window.STREAMFORGE_PANEL_INTERNAL_INITIAL)||'/';
  const persistInternal=(value)=>{
    const next=internalValue(value)||'/';
    internalState=next;
    writeCookie(next);
    return next;
  };
  try{
    Object.defineProperty(window,'STREAMFORGE_INTERNAL_PANEL_URL',{
      configurable:true,
      get:()=>internalState,
      set:value=>{persistInternal(value);},
    });
  }catch(_){window.STREAMFORGE_INTERNAL_PANEL_URL=internalState;}
  persistInternal(internalState);
  window.STREAMFORGE_FIXED_PANEL_ADDRESS_BAR=true;

  // STREAMFORGE_MAIN_NATIVE_BROWSER_HISTORY_V1077:
  // The Panel keeps a canonical/hidden address bar, but every real internal
  // navigation now owns a genuine browser history entry.  Chrome/Safari Back
  // and Forward therefore traverse Dashboard/Channels/Logs/Settings/etc.
  // history.state carries the hidden internal route and its scroll position.
  const panelHistoryKey='streamforgePanelV1077';
  const readPanelHistory=(state=history.state)=>{
    try{
      const item=state&&typeof state==='object'?state[panelHistoryKey]:null;
      if(!item||typeof item!=='object') return null;
      const route=internalValue(String(item.route||''));
      if(!route) return null;
      return {route,y:Math.max(0,Number(item.y)||0),depth:Math.max(0,Number(item.depth)||0)};
    }catch(_){return null;}
  };
  const panelHistoryState=(route,y,depth)=>{
    const state=history.state&&typeof history.state==='object'?{...history.state}:{};
    state[panelHistoryKey]={route:internalValue(route)||'/',y:Math.max(0,Math.round(Number(y)||0)),depth:Math.max(0,Math.round(Number(depth)||0))};
    return state;
  };
  const replacePanelHistory=(route=internalState,y=window.scrollY,depth=null)=>{
    const prior=readPanelHistory();
    const nextDepth=depth===null?(prior?.depth||0):depth;
    try{history.replaceState(panelHistoryState(route,y,nextDepth),'',canonical)}catch(_){}
  };
  const pushPanelHistory=(route)=>{
    const prior=readPanelHistory();
    replacePanelHistory(internalState,window.scrollY,prior?.depth||0);
    const target=persistInternal(route);
    try{history.pushState(panelHistoryState(target,0,(prior?.depth||0)+1),'',canonical)}catch(_){replacePanelHistory(target,0,(prior?.depth||0)+1)}
    return target;
  };
  const currentPanelDepth=()=>readPanelHistory()?.depth||0;
  try{history.scrollRestoration='manual'}catch(_){}
  const existingPanelHistory=readPanelHistory();
  // A server render is authoritative.  Sync a direct URL/POST redirect into
  // the current browser entry without creating a duplicate entry.
  if(!existingPanelHistory||existingPanelHistory.route!==internalState){
    replacePanelHistory(internalState,existingPanelHistory?.y||0,existingPanelHistory?.depth||0);
  }
  const restoreNativeHistoryScroll=()=>{
    const item=readPanelHistory();
    if(!item||item.route!==internalState) return;
    const apply=()=>window.scrollTo({top:item.y,left:0,behavior:'auto'});
    requestAnimationFrame(()=>requestAnimationFrame(apply));
    setTimeout(apply,120);
    setTimeout(apply,420);
  };
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',restoreNativeHistoryScroll,{once:true}); else restoreNativeHistoryScroll();
  let panelScrollTimer=0;
  window.addEventListener('scroll',()=>{
    clearTimeout(panelScrollTimer);
    panelScrollTimer=setTimeout(()=>{
      const item=readPanelHistory();
      if(item&&item.route===internalState) replacePanelHistory(internalState,window.scrollY,item.depth);
    },80);
  },{passive:true});
  window.addEventListener('popstate',(event)=>{
    const item=readPanelHistory(event.state);
    if(!item) return;
    const route=internalValue(item.route);
    if(!route) return;
    persistInternal(route);
    document.documentElement.classList.add('streamforge-native-loading');
    requestAnimationFrame(()=>location.reload());
  });

  const isSmartBackLink=link=>{
    if(!(link instanceof HTMLAnchorElement)) return false;
    if(link.dataset.streamforgeBack==='1') return true;
    const text=String(link.textContent||'').trim().replace(/\s+/g,' ');
    return /^(?:back(?:\s+to\b|\b)|go back\b)/i.test(text);
  };

  const markLoading=(target)=>{
    document.documentElement.classList.add('streamforge-native-loading');
    target?.classList?.add('streamforge-nav-loading');
  };
  const reloadCanonical=()=>{
    // The visible URL is already canonical; reload guarantees a fresh normal
    // document request instead of the old in-place fetch/document.write flow.
    requestAnimationFrame(()=>location.reload());
  };
  const patchLink=(link)=>{
    if(!(link instanceof HTMLAnchorElement)||link.hasAttribute('download')) return;
    const hrefRaw=link.getAttribute('href')||'';
    const raw=link.dataset.streamforgeNavUrl||link.dataset.sfNavUrl||hrefRaw;
    if(!raw||raw.startsWith('#')||raw.startsWith('javascript:')||raw.startsWith('mailto:')||raw.startsWith('tel:')) return;
    const target=internalValue(raw);
    if(target){
      link.dataset.streamforgeNavUrl=target;
      if(hideHover){
        // Hide even the configured same-origin Panel URL from browser hover/status previews.
        link.removeAttribute('href');
        link.setAttribute('role','link');
        if(!link.hasAttribute('tabindex')) link.tabIndex=0;
      }else{
        // Keep legacy canonical-root preview when the operator disables hiding.
        link.setAttribute('href',canonical);
      }
      return;
    }
    if(!hideHover||!hrefRaw) return;
    try{
      const u=new URL(hrefRaw,location.href);
      if(u.origin!==location.origin) return;
      link.dataset.sfHoverDirectUrl=u.pathname+u.search+u.hash;
      link.removeAttribute('href');
      link.setAttribute('role','link');
      if(!link.hasAttribute('tabindex')) link.tabIndex=0;
    }catch(_){}
  };
  const patch=(scope=document)=>{
    if(scope instanceof HTMLAnchorElement) patchLink(scope);
    if(scope?.querySelectorAll) scope.querySelectorAll('a[href],a[data-streamforge-nav-url],a[data-sf-nav-url]').forEach(patchLink);
    const forms=[];
    if(scope instanceof HTMLFormElement) forms.push(scope);
    if(scope?.querySelectorAll) forms.push(...scope.querySelectorAll('form'));
    forms.forEach(form=>{
      const method=(form.getAttribute('method')||'get').toLowerCase();
      if(method==='get') return;
      const raw=form.getAttribute('action')||internalState||'/';
      const fixed=addRoot(raw);
      if(fixed!==raw) form.setAttribute('action',fixed);
    });
  };
  patch(document);
  new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>{
    if(node.nodeType===1) patch(node);
  }))).observe(document.documentElement,{childList:true,subtree:true});

  // STREAMFORGE_MAIN_HIDDEN_HLS_TARGET_FIX_V1013:
  // Same-origin direct links lose href while hover masking is enabled.
  // Explicitly open target=_blank/modified clicks so HLS action buttons remain usable.
  document.addEventListener('keydown',(event)=>{
    if(!hideHover||(event.key!=='Enter'&&event.key!==' ')) return;
    const link=event.target?.closest?.('a[data-streamforge-nav-url],a[data-sf-nav-url],a[data-sf-hover-direct-url]');
    if(!link) return;
    const target=link.dataset.streamforgeNavUrl||link.dataset.sfNavUrl||'';
    if(target&&isPanelPath(target)){
      event.preventDefault();
      pushPanelHistory(target);
      markLoading(link);
      reloadCanonical();
      return;
    }
    const direct=link.dataset.sfHoverDirectUrl||'';
    if(direct){
      event.preventDefault();
      const wantsNew=(link.target&&link.target.toLowerCase()!=='_self');
      if(wantsNew) window.open(direct,'_blank','noopener');
      else location.assign(direct);
    }
  });

  document.addEventListener('click',(event)=>{
    if(event.defaultPrevented||event.button!==0) return;
    const link=event.target?.closest?.('a');
    if(!link||link.hasAttribute('download')) return;
    const target=link.dataset.streamforgeNavUrl||link.dataset.sfNavUrl||'';
    if(!target||!isPanelPath(target)){
      const direct=hideHover?(link.dataset.sfHoverDirectUrl||''):'';
      if(!direct) return;
      const wantsNew=Boolean(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||(link.target&&link.target.toLowerCase()!=='_self'));
      event.preventDefault();
      event.stopImmediatePropagation();
      if(wantsNew) window.open(direct,'_blank','noopener');
      else location.assign(direct);
      return;
    }
    const wantsNew=Boolean(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||(link.target&&link.target.toLowerCase()!=='_self'));
    if(!wantsNew&&isSmartBackLink(link)&&currentPanelDepth()>0){
      markLoading(link);
      event.preventDefault();
      event.stopImmediatePropagation();
      history.back();
      return;
    }
    // Modified/new-tab clicks retain the legacy cookie hand-off. Normal
    // same-tab navigation gets a real browser history entry.
    if(wantsNew){persistInternal(target);markLoading(link);return;}
    pushPanelHistory(target);
    markLoading(link);
    event.preventDefault();
    event.stopImmediatePropagation();
    reloadCanonical();
  },true);

  document.addEventListener('submit',(event)=>{
    const form=event.target;
    if(!(form instanceof HTMLFormElement)) return;
    const method=(form.getAttribute('method')||'get').toLowerCase();
    // STREAMFORGE_BULK_NODE_ASSIGNMENT_NO_NATIVE_SPINNER_V1125:
    // The Channels bulk Node actions are handled by app.js with an in-place
    // JSON request. Do not set the document-wide progress cursor before that
    // handler gets a chance to prevent the native POST.
    const submitValue=String(event.submitter?.value||'');
    if(form.id==='channel-bulk-form'&&['nodes_add','nodes_remove','nodes_set'].includes(submitValue)) return;
    if(method!=='get'||form.target) { markLoading(event.submitter||form); return; }
    const submitter=event.submitter;
    const raw=(submitter&&submitter.getAttribute('formaction'))||form.getAttribute('action')||internalState||'/';
    const targetBase=internalValue(raw);
    if(!targetBase) { markLoading(event.submitter||form); return; }
    const url=new URL(targetBase,location.origin);
    const params=new URLSearchParams();
    for(const [key,value] of new FormData(form,submitter||undefined).entries()){
      if(typeof value==='string'&&value!=='') params.append(key,value);
    }
    url.search=params.toString();
    pushPanelHistory(url.pathname+url.search);
    markLoading(event.submitter||form);
    event.preventDefault();
    event.stopImmediatePropagation();
    reloadCanonical();
  },true);
})();

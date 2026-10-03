#!/usr/bin/env node
// Real Edge rendering and deterministic SSE fixtures; no production fixture route.
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const root = path.resolve(import.meta.dirname, "../../..");
const fixtureFile = path.join(root, "frontend/tests/visual/fixtures.json");
const outputDir = path.join(root, "frontend/tests/visual/screenshots");
const edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge";
const base = "http://127.0.0.1:8000";
const viewport = { width: 390, height: 844 };
const log = [];
let designTargets = null;
let designCopy = null;
const designSelectors = {
  train_schedule:{topbar:[".main > header > .icon-btn",".main > header .title",".main > header .new-chat-top"],prompt:".row.user .bubble",result_card:".schedule-card",title:".schedule-head",route_summary:".schedule-route",date_duration:".schedule-meta",station_timeline_table:".schedule-table-wrap",source_note:".schedule-note",source_details_row:".schedule-source",result_actions:[".row.assistant > .actions > button:not([title^='分享'])"],suggestions:[".row.assistant > .followups > button"],fixed_input:"#input"},
  emu_routing:{topbar:[".main > header > .icon-btn",".main > header .title",".main > header .new-chat-top"],prompt:".row.user .bubble",result_card:".routing-card",title_date:".routing-card .schedule-head",date_record:".routing-date",time_semantics_note:".routing-card .schedule-note",records_table:".routing-card .schedule-table-wrap",source_details_row:".routing-card .schedule-source",batch_action:".routing-batch",integrity_note:".routing-integrity-note",fixed_input:"#input"},
  history:{topbar:".history-header",search:".history-search",create_action:".history-create",today_group_and_rows:[".history-list > :nth-child(1)",".history-list > :nth-child(2)",".history-list > :nth-child(3)",".history-list > :nth-child(4)"],yesterday_group_and_rows:[".history-list > :nth-child(5)",".history-list > :nth-child(6)"],action_panel:".history-action-panel",cancel_action:".history-action-cancel",fixed_bottom_links:{selectors:[".history-footer > button"],content:true}},
  query_loading:{loading_card:".query-loading",spinner:{selectors:[".query-spinner"],layout:true},loading_title_and_stage:{selectors:[".query-loading strong",".query-loading-stage"],text:true},skeletons:[".query-skeletons > *"],editable_input_and_stop:{selectors:["#input","#send"],origin:"self"},preserve_note:{selectors:[".query-loading-foot"],text:true,origin:"self"}},
  batch_partial:{batch_card:".batch-card",retry_failed_only:".batch-card + .routing-batch",retain_success_note:".batch-retain"},
  routing_empty:{empty_icon_group:[".empty-routing-mark img"],empty_title:{selectors:[".empty-routing-title"],text:true},empty_message:{selectors:[".empty-routing-copy"],text:true},date_pill:".empty-routing-date",change_date_action:".empty-routing-actions .routing-batch",recent_records_action:".empty-routing-actions .routing-recent",history_note:{selectors:[".empty-routing-foot"],text:true}},
  connection_error:{error_icon_and_title:{selectors:[".connection-title-row"],content:true},error_explanation:{selectors:[".connection-error-copy"],text:true},retry_action:".connection-retry",settings_action:".connection-settings",error_details_collapsed:".connection-error details",api_key_label:{selectors:[".connection-key-label"],text:true},api_key_field:".connection-key-invalid",field_error:{selectors:[".connection-key-error"],text:true}},
  reading_followup:{answer_intro:{selectors:[".row.assistant.has-table-answer .md"],firstText:true},schedule_table:".row.assistant.has-table-answer .table-wrap",return_to_bottom_cue:{selectors:["#new-content-cue"],origin:"self"},followup_actions:[".row.assistant.has-table-answer .followups > button"],fixed_input_and_send:{selectors:["#input","#send"],origin:"self"},reading_hint:{selectors:[".reading-guidance"],text:true,origin:"self"}},
  query_details:{details_panel:[".request-details > summary",".request-details-body"],details_heading_and_source:[".request-details > summary",".request-details-body > .request-detail-row:first-child"],date_row:".request-details-body > .request-detail-row:nth-child(2)",time_basis_row:".request-details-body > .request-detail-row:nth-child(3)",latency_row:".request-details-body > .request-detail-row:nth-child(4)",usage_row:".request-details-body > .request-detail-row:nth-child(5)",technical_log_action:".request-tech"},
};
const designModules = {
  query_loading:{label:"01 / 正在查询",reference:"desi2.png",panelWidthPx:459},
  batch_partial:{label:"02 / 部分查询成功",reference:"desi2.png",panelWidthPx:454},
  routing_empty:{label:"03 / 暂无记录",reference:"desi2.png",panelWidthPx:462},
  connection_error:{label:"04 / 连接失败",reference:"desi2.png",panelWidthPx:458},
  reading_followup:{label:"05 / 阅读与追问",reference:"desi2.png",panelWidthPx:454},
  query_details:{label:"06 / 来源与查询详情",reference:"desi2.png",panelWidthPx:462},
};

async function waitFor(fn, label, ms = 12000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    const value = await fn();
    if (value) return value;
    await delay(100);
  }
  throw new Error(`timeout waiting for ${label}`);
}

async function requestJson(url, method = "GET") {
  const response = await fetch(url, { method });
  if (!response.ok) throw new Error(`${method} ${url}: HTTP ${response.status}`);
  return response.json();
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.events = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) {
        const waiters = this.events.get(message.method) || [];
        this.events.delete(message.method);
        for (const resolve of waiters) resolve(message.params || {});
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }

  waitEvent(name, timeout = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`timeout waiting for CDP event ${name}`)), timeout);
      const waiters = this.events.get(name) || [];
      waiters.push((value) => { clearTimeout(timer); resolve(value); });
      this.events.set(name, waiters);
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = ++this.nextId;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.ws.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression, awaitPromise = true) {
    const value = await this.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise });
    if (value.exceptionDetails) throw new Error(value.exceptionDetails.text || "Runtime evaluation failed");
    return value.result?.value;
  }

  close() { this.ws.close(); }
}

const bootstrap = String.raw`(() => {
  const nativeFetch = window.fetch.bind(window);
  window.__railfanVisualRequests = [];
  window.__railfanVisualRequestState = "idle";
  const line = value => ` + "`data: ${JSON.stringify(value)}\\n\\n`" + String.raw`;
  const encoder = new TextEncoder();
  const streamAnswer = "固定验收回复\n" + Array.from({length:18}, (_,i) => "读取位置保持验收内容 " + (i+1)).join("\n");
  window.fetch = (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.endsWith("/api/chat/stream")) return nativeFetch(input, init);
    const fixture = localStorage.getItem("__railfan_visual_fixture") || "";
    let body = {};
    try { body = JSON.parse(init.body || "{}"); } catch { }
    window.__railfanVisualRequests.push(body);
    const signal = init.signal || (input instanceof Request ? input.signal : null);
    if (fixture === "query_loading" || fixture === "reading_followup") {
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          window.__railfanVisualRequestState = "open";
          controller.enqueue(encoder.encode(line({type:"stage", stage:"intent", msg:"schedule · realtime", recognized:"G8932"}) + line({type:"stage", stage:"retrieve", msg:"train.schedule"})));
          window.__railfanVisualRelease = () => {
            if (window.__railfanVisualRequestState !== "open") return;
            controller.enqueue(encoder.encode(line({type:"answer", delta:streamAnswer}) + line({type:"done", intent:"列车时刻查询", question_type:"realtime", slots:[], sources:[], tool_trace:[], thinking:"", answer_done:true, usage:{total_tokens:1}, latency_ms:1, process_logs:[], display_results:[]})));
            controller.close(); window.__railfanVisualRequestState = "completed";
          };
          if (signal) signal.addEventListener("abort", () => {
            window.__railfanVisualRequestState = "aborted";
            try { controller.error(new DOMException("The operation was aborted.", "AbortError")); } catch { }
          }, {once:true});
          if (signal?.aborted) {
            window.__railfanVisualRequestState = "aborted";
            controller.error(new DOMException("The operation was aborted.", "AbortError"));
          }
        },
      }), {headers:{"Content-Type":"text/event-stream"}}));
    }
    return Promise.resolve(new Response(new ReadableStream({
      start(controller) {
        window.__railfanVisualRequestState = "open";
        controller.enqueue(encoder.encode(line({type:"stage", stage:"intent", msg:"schedule · realtime", recognized:"G8932"}) + line({type:"stage", stage:"retrieve", msg:"train.schedule"})));
        const events = fixture === "connection_error"
          ? [{type:"error", message:"LLM 鉴权失败(HTTP 401)：API Key 无效或被拒绝"}]
          : [{type:"answer", delta:"固定验收回复"}, {type:"done", intent:"列车时刻查询", question_type:"realtime", slots:[], sources:[], tool_trace:[], thinking:"", answer_done:true, usage:{total_tokens:1}, latency_ms:1, process_logs:[], display_results:[]}];
        for (const event of events) controller.enqueue(encoder.encode(line(event)));
        controller.close(); window.__railfanVisualRequestState = "completed";
      },
    }), {headers:{"Content-Type":"text/event-stream"}}));
  };
})();`;

function conversation(id, title, updatedAt, messages) {
  return { id, title, titleAuto: false, createdAt: updatedAt, updatedAt, messages };
}

function makeState(name, states, now) {
  const fixture = states[name];
  const id = `fixture_${name}`;
  if (name === "query_loading") return { current: id, route: `#/c/${id}`, conversations: [conversation(id, "正在查询", now, [])] };
  if (name === "history") {
    const msg = (text, result, intent) => [{role:"user",content:text},{role:"assistant",content:"固定验收记录",meta:{intent,displayResults:result?[result]:[]}}];
    return { current:"history_today3", route:"#/history", conversations:[
      conversation("history_today3", "北京南到上海虹桥票价", now, [
        {role:"user",content:"G1 · 明天出发"},{role:"assistant",content:"已记录你的票价查询。",meta:{intent:"票价查询"}},
      ]),
      conversation("history_today", "CR400BF-5033 今日交路", now-120000, msg("查询今天交路",states.emu_routing,"车组交路查询")),
      conversation("history_today2", "G8932 列车时刻", now-240000, msg("查询 G8932",states.train_schedule,"列车时刻查询")),
      conversation("history_yesterday", "京沪线沿线车站", now-86400000, msg("查看京沪线沿线车站",null,"")),
    ]};
  }
  const meta = {intent:"列车时刻查询", questionType:"realtime", displayResults:[], processLogs:["[数据检索] 固定验收夹具"], usage:{total_tokens:1555}, latencyMs:5800};
  let content = "查询结果如下。";
  if (["train_schedule","emu_routing","batch_partial","routing_empty"].includes(name)) meta.displayResults = [fixture];
  if (name === "emu_routing" || name === "batch_partial" || name === "routing_empty") meta.intent = "车组交路查询";
  if (name === "reading_followup") {
    const stops=states.train_schedule.stops;
    content = [
      "以下是 G8932 次列车的时刻信息：",
      "| 站次 | 车站 | 到达 | 出发 | 停留 |",
      "| --- | --- | --- | --- | --- |",
      ...stops.map(stop => `| ${stop.station_no} | ${stop.station} | ${stop.arrive_time} | ${stop.start_time} | ${stop.stopover_time || "—"} |`),
    ].join("\n");
  }
  if (name === "query_details") { meta.displayResults=[states.train_schedule]; meta.usage=fixture.usage; meta.latencyMs=fixture.latency_ms; }
  if (name === "connection_error") content = "";
  const prompts = {train_schedule:"查一下 G8932 今天的时刻表",emu_routing:"CR400BF-5033 今天的交路",batch_partial:"查询这三趟车的时刻表",routing_empty:"CR400BF-5033 今天的交路",connection_error:"查一下 G8932 今天的时刻表",reading_followup:"以下是 G8932 次列车的时刻信息",query_details:"查一下 G8932 今天的时刻表"};
  const messages = name === "connection_error" ? [] : name === "reading_followup"
    ? [
        {role:"user",content:"秦皇岛到北京南有哪些车次？"},{role:"assistant",content:"可以先查看直达列车的时刻信息。"},
        {role:"user",content:"我想看 G8932 的经停站。"},{role:"assistant",content:"下面列出该车次的站点与时刻。"},
        {role:"user",content:prompts.reading_followup},{role:"assistant",content,meta},
      ]
    : [{role:"user",content:prompts[name] || ""},{role:"assistant",content,meta}];
  return { current:id, route:`#/c/${id}`, conversations:[conversation(id, name, now, messages)] };
}

async function waitSelector(cdp, selector, visible = false) {
  try {
    return await waitFor(async () => cdp.evaluate(`(() => { const e=document.querySelector(${JSON.stringify(selector)}); if(!e)return false; const r=e.getBoundingClientRect(),s=getComputedStyle(e); return ${visible ? "r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'" : "true"}; })()`), selector);
  } catch (error) {
    const text = await cdp.evaluate("({url:location.href,hash:location.hash,stored:localStorage.getItem('railfan_current_conv_v1'),convs:(JSON.parse(localStorage.getItem('railfan_conversations_v1')||'[]')).map(c=>({id:c.id,messages:c.messages?.length})),page:document.querySelector('#chat')?.innerText || document.body.innerText})").catch(() => "");
    throw new Error(`${error.message}; page=${JSON.stringify(text).slice(0,1000)}`);
  }
}

async function screenshot(cdp, name, expected = {}) {
  if (name !== "reading_followup" && name !== "history" && !expected.preserveChatScroll) {
    await cdp.evaluate("if(document.querySelector('#chat')) document.querySelector('#chat').scrollTop=0");
  }
  const actual = await cdp.evaluate("({width:innerWidth,height:innerHeight,dpr:devicePixelRatio})");
  if (actual.width !== viewport.width || actual.height !== viewport.height) throw new Error(`${name}: CSS viewport ${JSON.stringify(actual)}`);
  const measurements = await cdp.evaluate(`(() => { const selectors=['header','header .title','.row.user','.row.user .bubble','.row.assistant','.row.assistant .bubble','.schedule-card','.schedule-head','.schedule-route','.schedule-meta','.schedule-table-wrap','.schedule-note','.schedule-source','.request-details','.request-details[open] > summary','.request-detail-row','.routing-card','.routing-date','.routing-batch','.routing-integrity-note','.query-loading','.query-spinner','.query-loading strong','.query-loading-stage','.query-skeletons','.query-loading-foot','.connection-error','.connection-title-row','.connection-error-copy','.connection-error-actions','.connection-error details','.connection-key-label','.connection-key-invalid','.connection-key-error','.batch-card','.batch-item','.batch-card + .routing-batch','.batch-retain','.empty-routing-card','.empty-routing-mark','.empty-routing-title','.empty-routing-copy','.empty-routing-date','.empty-routing-actions','.empty-routing-actions .routing-batch','.empty-routing-actions .routing-recent','.history-page','.history-search','.history-create','.history-action-panel','.history-footer','.actions','.followups','.row.assistant.has-table-answer .table-wrap','.row.assistant.has-table-answer table','#new-content-cue','footer','#input','#send']; const out={}; for(const s of selectors){const e=document.querySelector(s);if(!e)continue;const r=e.getBoundingClientRect(),c=getComputedStyle(e);out[s]={x:+(r.x.toFixed(2)),y:+(r.y.toFixed(2)),width:+(r.width.toFixed(2)),height:+(r.height.toFixed(2)),cssWidth:c.width,flex:c.flex,alignSelf:c.alignSelf};} return out;})()`);
  const target = expected.target || null;
  const checks = target ? Object.entries(target).map(([selector, rect]) => {
    const measured = measurements[selector];
    if (!measured) return {selector,target:rect,measured:null,result:"fail",reason:"element not measured"};
    const delta = Object.fromEntries(["x","y","width","height"].map(k=>[k,Math.round((measured[k]-rect[k])*100)/100]));
    const pass = Object.values(delta).every(v=>Math.abs(v)<=2);
    return {selector,target:rect,measured:{x:measured.x,y:measured.y,width:measured.width,height:measured.height},deltaPx:delta,result:pass?"pass":"fail",tolerancePx:2};
  }) : [{selector:null,target:null,measured:null,result:"incomplete",reason:"尚未从附件栅格提取可复核的目标矩形"}];
  const spec=designTargets?.states?.[name];
  const designChecks=spec?await cdp.evaluate(`(()=>{
    const spec=${JSON.stringify(spec)}, map=${JSON.stringify(designSelectors[name]||{})};
    const frame=spec.frame, phone=spec.source==='desi1.png', scale=390/frame[2];
    const origins={
      query_loading:{raw:'loading_card',selector:'.query-loading'},
      batch_partial:{raw:'batch_card',selector:'.batch-card'},
      routing_empty:{raw:'module_frame',selector:'.empty-routing-card'},
      connection_error:{raw:'module_frame',selector:'.connection-error'},
      reading_followup:{raw:'answer_intro',selector:{selectors:['.row.assistant.has-table-answer .md'],firstText:true}},
      query_details:{raw:'details_panel',selector:['.request-details > summary','.request-details-body']},
    };
    function box(rule){
      const conf=typeof rule==='string'?{selectors:[rule]}:Array.isArray(rule)?{selectors:rule}:rule;
      const rects=[];
      for(const selector of conf.selectors){
        for(const node of document.querySelectorAll(selector)){
          const style=getComputedStyle(node), outer=node.getBoundingClientRect();
          if(!outer.width||!outer.height||style.display==='none'||style.visibility==='hidden')continue;
          let rect=outer;
          if(conf.layout){const width=parseFloat(style.width),height=parseFloat(style.height);rect={left:outer.left+outer.width/2-width/2,top:outer.top+outer.height/2-height/2,right:outer.left+outer.width/2+width/2,bottom:outer.top+outer.height/2+height/2,width,height};}
          if(conf.text||conf.firstText){const range=document.createRange();range.selectNodeContents(conf.firstText?node.firstChild:node);rect=range.getBoundingClientRect();}
          if(conf.content){
            for(const child of node.childNodes){
              if(child.nodeType===Node.TEXT_NODE&&child.textContent.trim()){
                const range=document.createRange();range.selectNodeContents(child);const part=range.getBoundingClientRect();if(part.width&&part.height)rects.push(part);
              }else if(child.nodeType===Node.ELEMENT_NODE){
                const part=child.getBoundingClientRect();if(part.width&&part.height)rects.push(part);
              }
            }
            continue;
          }
          if(rect.width&&rect.height)rects.push(rect);
        }
      }
      if(!rects.length)return null;
      const left=Math.min(...rects.map(r=>r.left)),top=Math.min(...rects.map(r=>r.top));
      const right=Math.max(...rects.map(r=>r.right)),bottom=Math.max(...rects.map(r=>r.bottom));
      return {x:left,y:top,width:right-left,height:bottom-top};
    }
    const out=[];
    for(const [key,rule] of Object.entries(map)){
      const raw=spec.elements[key], actual=box(rule), selector=typeof rule==='string'?rule:JSON.stringify(rule);
      if(!raw||!actual){out.push({element:key,selector,targetRaw:raw||null,result:'unmapped',reason:!raw?'original rectangle missing':'visible matching element missing'});continue;}
      const conf=typeof rule==='object'&&!Array.isArray(rule)?rule:{};
      let baseRaw=frame,baseActual={x:0,y:0},basis='phone viewport';
      if(!phone){
        const origin=conf.origin==='self'?{raw:key,selector:null}:origins[${JSON.stringify(name)}];
        baseRaw=origin.raw==='module_frame'?frame:spec.elements[origin.raw];
        baseActual=conf.origin==='self'?actual:box(origin.selector);
        basis=conf.origin==='self'?'component-local dimensions':origin.raw+' component-local coordinates';
        if(!baseRaw||!baseActual){out.push({element:key,selector,targetRaw:raw,result:'unmapped',reason:'semantic origin missing'});continue;}
      }
      const target={x:(raw[0]-baseRaw[0])*scale,y:(raw[1]-baseRaw[1])*scale,width:raw[2]*scale,height:raw[3]*scale};
      const measured={x:actual.x-baseActual.x,y:actual.y-baseActual.y,width:actual.width,height:actual.height};
      const delta=Object.fromEntries(['x','y','width','height'].map(k=>[k,+((measured[k]-target[k]).toFixed(2))]));
      const designConflict=${JSON.stringify(name)}==='reading_followup'&&key==='fixed_input_and_send';
      const result=designConflict?'design_conflict':Object.values(delta).every(v=>Math.abs(v)<=2)?'pass':'fail';
      out.push({element:key,selector,semanticBasis:conf.text||conf.firstText?'text ink':conf.content?'button content union':Array.isArray(rule)||conf.selectors?.length>1?'union of matching elements':'element box',coordinateBasis:basis,targetRaw:raw,targetCss:Object.fromEntries(Object.entries(target).map(([k,v])=>[k,+v.toFixed(2)])),measured:Object.fromEntries(Object.entries(measured).map(([k,v])=>[k,+v.toFixed(2)])),measuredViewport:{x:+actual.x.toFixed(2),y:+actual.y.toFixed(2),width:+actual.width.toFixed(2),height:+actual.height.toFixed(2)},deltaPx:delta,result,reason:designConflict?'desi1 full-phone footer height 51.41 CSS px conflicts with desi2 module height 42.95 CSS px':undefined,tolerancePx:2});
    }
    return out;
  })()`):[];
  for (const selector of expected.visible || []) await waitSelector(cdp, selector, true);
  const image = await cdp.send("Page.captureScreenshot", {format:"png",fromSurface:true,captureBeyondViewport:false,omitBackground:false});
  await fs.writeFile(path.join(outputDir, `${name}.png`), Buffer.from(image.data, "base64"));
  const module=designModules[name];
  const visibleText=await cdp.evaluate("(()=>({body:document.body.innerText,placeholders:[...document.querySelectorAll('input,textarea')].map(e=>e.placeholder).filter(Boolean),values:[...document.querySelectorAll('input,textarea')].map(e=>e.value).filter(Boolean)}))()");
  const iconInventory=await cdp.evaluate("[...document.querySelectorAll('img[src$=\".svg\"]')].map((node)=>{const r=node.getBoundingClientRect(),s=getComputedStyle(node);return {asset:new URL(node.src).pathname.split('/').pop(),parent:node.parentElement?.className||'',x:+r.x.toFixed(2),y:+r.y.toFixed(2),width:+r.width.toFixed(2),height:+r.height.toFixed(2),visible:!!(r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden')}}).filter(item=>item.visible)");
  const expectedCopy=designCopy?.states?.[name]||[];
  const copyChecks=await cdp.evaluate(`(()=>{
    const entries=${JSON.stringify(expectedCopy)};
    return entries.map(entry=>{
      const node=document.querySelector(entry.selector),r=node?.getBoundingClientRect(),style=node?getComputedStyle(node):null;
      const visible=!!(node&&r.width&&r.height&&style.display!=='none'&&style.visibility!=='hidden');
      let actual=null;
      if(visible){
        if(entry.mode==='placeholder')actual=node.getAttribute('placeholder');
        else if(entry.mode==='value')actual=node.value;
        else if(entry.mode==='aria-label')actual=node.getAttribute('aria-label');
        else if(entry.mode==='firstText')actual=node.firstChild?.textContent.trim()??null;
        else actual=node.textContent.trim();
      }
      const expectedChars=Array.from(entry.expected),actualChars=Array.from(actual??'');
      const firstDifference=actual===entry.expected?null:Array.from({length:Math.max(expectedChars.length,actualChars.length)},(_,i)=>i).find(i=>expectedChars[i]!==actualChars[i]);
      return {...entry,actual,visible,result:visible&&actual===entry.expected?'pass':'fail',firstDifference};
    });
  })()`);
  log.push({state:name,viewport:actual,path:path.join(outputDir,`${name}.png`),designReference:module?{file:module.reference,module:module.label,sourcePanelWidthPx:spec.frame[2],normalizationTo390:390/spec.frame[2],overlayPath:path.join(root,"frontend/tests/visual/design-overlays",`${name}.svg`),coordinateRule:"独立应用模块仅比较内部几何，不使用整页绝对坐标"}:(["train_schedule","emu_routing","history"].includes(name)?{file:"desi1.png",applicationFramePx:spec.frame,normalizationTo390:390/spec.frame[2],overlayPath:path.join(root,"frontend/tests/visual/design-overlays",`${name}.svg`),coordinateRule:"以应用内容区域左上角为原点；设备外壳与系统示意在测量框外"}:null),measurements,iconInventory,visibleText,copyChecks,stateSnapshots:expected.checks||[],checks:[...(designChecks.length?checks.filter(c=>c.selector):checks),...designChecks]});
}

async function main() {
  const fixture = JSON.parse(await fs.readFile(fixtureFile,"utf8"));
  designTargets=JSON.parse(await fs.readFile(path.join(root,"frontend/tests/visual/design-targets.json"),"utf8"));
  designCopy=JSON.parse(await fs.readFile(path.join(root,"frontend/tests/visual/design-copy.json"),"utf8"));
  const measurementMap=Object.fromEntries(Object.entries(designSelectors).map(([state,rules])=>{
    const spec=designTargets.states[state];
    return [state,{source:spec.source,sourceFrame:spec.frame,cssScale:390/spec.frame[2],rules:Object.fromEntries(Object.entries(rules).map(([element,selector])=>[element,{sourceRect:spec.elements[element],pageSelector:selector}]))}];
  }));
  await fs.writeFile(path.join(root,"frontend/tests/visual/measurement-map.json"),JSON.stringify(measurementMap,null,2)+"\n");
  const profile = await fs.mkdtemp(path.join(os.tmpdir(),"railfan-edge-"));
  await fs.mkdir(outputDir,{recursive:true});
  const server = spawn(path.join(root,"backend/.venv/bin/uvicorn"),["app.main:app","--app-dir",path.join(root,"backend"),"--host","127.0.0.1","--port","8000"],{cwd:root,stdio:"ignore",env:{...process.env,LLM_MOCK:"true"}});
  const browser = spawn(edge,["--headless=new","--disable-gpu","--no-first-run","--no-default-browser-check","--remote-allow-origins=*","--remote-debugging-port=0",`--user-data-dir=${profile}`,"about:blank"],{stdio:"ignore"});
  let cdp;
  try {
    await waitFor(async()=>{try{const r=await fetch(base);return r.ok;}catch{return false;}},"FastAPI frontend");
    const activeFile=path.join(profile,"DevToolsActivePort");
    const port=await waitFor(async()=>{try{return Number((await fs.readFile(activeFile,"utf8")).split("\n")[0]);}catch{return false;}},"Edge DevTools port");
    const targets=await waitFor(async()=>{try{return await requestJson(`http://127.0.0.1:${port}/json/list`);}catch{return false;}},"Edge page target");
    const target=targets.find(item=>item.type==="page");
    cdp=new CDP(target.webSocketDebuggerUrl);
    await cdp.send("Page.enable"); await cdp.send("Runtime.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true,screenWidth:390,screenHeight:844});
    await cdp.send("Page.addScriptToEvaluateOnNewDocument",{source:bootstrap});
    const firstLoad=cdp.waitEvent("Page.loadEventFired");
    await cdp.send("Page.navigate",{url:base});
    await firstLoad;
    await waitSelector(cdp,"#input");
    const now=Date.now();
    const names=["train_schedule","emu_routing","query_loading","batch_partial","routing_empty","connection_error","reading_followup","query_details","history"];
    for(const name of names){
      const data=makeState(name,fixture.states,now);
      // Move off the application page first so its beforeunload save cannot
      // overwrite the deterministic localStorage state being seeded below.
      const seedPage=cdp.waitEvent("Page.loadEventFired");
      await cdp.send("Page.navigate",{url:`${base}/assets/icons/train-logo.svg`});
      await seedPage;
      const providerFixture = name === "connection_error"
        ? {activeId:"openai",rememberKey:false,entries:[{id:"openai",label:"OpenAI",base_url:"https://api.openai.com/v1",model:"gpt-4o-mini",key:"",custom:false}]}
        : null;
      await cdp.evaluate(`localStorage.setItem('railfan_conversations_v1',${JSON.stringify(JSON.stringify(data.conversations))});localStorage.setItem('railfan_current_conv_v1',${JSON.stringify(data.current)});localStorage.setItem('railfan_theme','light');localStorage.setItem('railfan_theme_v2','1');localStorage.setItem('__railfan_visual_fixture',${JSON.stringify(name)});${providerFixture ? `localStorage.setItem('railfan_llm_v1',${JSON.stringify(JSON.stringify(providerFixture))});` : ""}`);
      const pageLoad=cdp.waitEvent("Page.loadEventFired");
      await cdp.send("Page.navigate",{url:`${base}/?visual_fixture=${name}${data.route}`});
      await pageLoad;
      await waitSelector(cdp, name==="history"?".history-item":"#input");
      await waitFor(()=>cdp.evaluate("document.querySelector('#llm-notice-text')?.textContent.includes('Mock 演示模式')"),"local mock provider status");
      await cdp.evaluate("document.querySelector('#llm-notice-close').click()");
      if(name==="train_schedule") await screenshot(cdp,name,{visible:[".schedule-card"],target:{".schedule-card":{x:62,y:162,width:313,height:467}}});
      else if(name==="emu_routing") {
        await screenshot(cdp,name,{visible:[".routing-card"],target:{".routing-card":{x:62,y:168,width:312,height:422}}});
        await cdp.evaluate("document.querySelector('.routing-record-link').click()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequests?.length===1"),"per-train schedule action");
        const action=await cdp.evaluate("window.__railfanVisualRequests[0].display_action");
        if(action?.kind!=="train_schedule_batch"||action.trains?.length!==1||action.trains[0]!==fixture.states.emu_routing.records[0].train_code) throw new Error(`routing row action mismatch: ${JSON.stringify(action)}`);
        log.push({state:name,interaction:"open one train schedule",payload:action});
      }
      else if(name==="query_loading"){
        await cdp.evaluate("document.querySelector('#input').value='查一下 G8932 今天的时刻表';document.querySelector('#input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#send').click();");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequestState==='open'"),"active fixture SSE");
        await waitSelector(cdp,".query-loading",true);
        const state=await cdp.evaluate("({button:document.querySelector('#send').getAttribute('aria-label'),interrupted:!!document.querySelector('.stopped-tag'),recognized:document.querySelector('.query-loading-stage').textContent})");
        if(state.button!=="停止生成"||state.interrupted) throw new Error(`loading state assertion failed: ${JSON.stringify(state)}`);
        await cdp.evaluate("const i=document.querySelector('#input');i.value='再看看明天的';i.dispatchEvent(new Event('input',{bubbles:true}))");
        await screenshot(cdp,name,{visible:[".query-loading","#send"],checks:[state]});
        await cdp.evaluate("document.querySelector('#send').click()");
        const stopped=await waitFor(()=>cdp.evaluate("window.__railfanVisualRequestState==='aborted'&&document.querySelector('#send').getAttribute('aria-label')==='发送'"),"request abort");
        const hasInput=await cdp.evaluate("[...document.querySelectorAll('.user .bubble')].some(e=>e.textContent.includes('G8932'))");
        if(!stopped||!hasInput) throw new Error(`stop did not abort the active request or retain user input: ${JSON.stringify(await cdp.evaluate("({state:window.__railfanVisualRequestState,button:document.querySelector('#send').getAttribute('aria-label'),users:[...document.querySelectorAll('.user .bubble')].map(x=>x.textContent)})"))}`);
        log.push({state:name,interaction:"stop",result:"request aborted; query remains in conversation"});
      } else if(name==="batch_partial"){
        await waitSelector(cdp,".batch-card + .routing-batch",true);
        await screenshot(cdp,name,{visible:[".batch-card"]});
        await cdp.evaluate("document.querySelector('.batch-card + .routing-batch').click()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequests?.length===1"),"failed-only retry action");
        const action=await cdp.evaluate("window.__railfanVisualRequests[0].display_action");
        const failed=fixture.states.batch_partial.items.filter(x=>x.status==="failed").map(x=>x.train_code);
        if(JSON.stringify(action?.trains)!==JSON.stringify(failed)) throw new Error(`retry payload mismatch: ${JSON.stringify(action)}`);
        const retained=await cdp.evaluate("({rows:document.querySelector('.batch-card').textContent,note:document.querySelector('.batch-retain')?.textContent||''})");
        if(!retained.rows.includes("C2203")||!retained.rows.includes("G8927")||!retained.note) throw new Error("successful batch rows disappeared after retry");
        log.push({state:name,interaction:"retry failed rows",payload:action,result:"successful rows retained"});
      } else if(name==="routing_empty"){
        await waitSelector(cdp,".empty-routing-card",true);
        await screenshot(cdp,name,{visible:[".empty-routing-card"]});
        await cdp.evaluate("const d=document.querySelector('.visually-hidden-date');d.value='2026-09-26';d.dispatchEvent(new Event('change',{bubbles:true}));");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequests?.length===1"),"date switch action");
        const dateAction=await cdp.evaluate("window.__railfanVisualRequests[0].display_action");
        await cdp.evaluate("document.querySelector('.empty-routing-card .routing-recent').click()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequests?.length===2"),"recent routing action");
        const recentAction=await cdp.evaluate("window.__railfanVisualRequests[1].display_action");
        if(dateAction?.date!=="2026-09-26"||recentAction?.date!==null) throw new Error(`empty follow-up payload mismatch: ${JSON.stringify([dateAction,recentAction])}`);
        log.push({state:name,interaction:"change date and recent records",payloads:[dateAction,recentAction]});
      } else if(name==="connection_error"){
        await cdp.evaluate("document.querySelector('#input').value='查询 G8932';document.querySelector('#input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#send').click();");
        await waitSelector(cdp,".connection-error",true);
        await cdp.evaluate("document.querySelector('.connection-error').scrollIntoView({block:'center'})");
        await screenshot(cdp,name,{visible:[".connection-error",".connection-error-actions"],preserveChatScroll:true});
        await cdp.evaluate("document.querySelector('.connection-error details summary')?.click()");
        await waitFor(()=>cdp.evaluate("document.querySelector('.connection-error details')?.open===true"),"error details expansion");
        await cdp.evaluate("document.querySelector('.connection-retry').click()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequests?.length===2"),"connection retry");
        const same=await cdp.evaluate("window.__railfanVisualRequests[0].message===window.__railfanVisualRequests[1].message");
        if(!same) throw new Error("connection retry did not reuse original prompt");
        log.push({state:name,interaction:"retry",result:"same original message resent"});
        await cdp.evaluate("document.querySelector('.connection-settings').click()");
        await waitFor(()=>cdp.evaluate("location.hash.startsWith('#/settings')"),"settings navigation");
        const keyField=await cdp.evaluate("({invalid:!!document.querySelector('.field-invalid'),message:document.querySelector('.field-error')?.textContent||'',hasSecret:/sk-[A-Za-z0-9_-]{8,}/.test(document.body.innerText)})");
        if(!keyField.invalid||!keyField.message||keyField.hasSecret) throw new Error(`API Key field recovery state mismatch: ${JSON.stringify(keyField)}`);
        log.push({state:name,interaction:"settings",result:{route:"settings",apiKeyFieldInvalid:keyField.invalid,fieldMessage:keyField.message,secretExposed:keyField.hasSecret}});
      } else if(name==="reading_followup"){
        await waitSelector(cdp,".md table",true);
        const before=await cdp.evaluate("({height:document.querySelector('#chat').scrollHeight,client:document.querySelector('#chat').clientHeight})");
        if(before.height<=before.client) throw new Error("reading fixture is not scrollable");
        await cdp.evaluate("document.querySelector('#input').value='已有草稿';document.querySelector('#input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.followup').click();");
        const input=await cdp.evaluate("document.querySelector('#input').value");
        if(!input.startsWith("已有草稿\n")) throw new Error(`follow-up overwrote existing draft: ${input}`);
        const requestsBeforeFollowupSend=await cdp.evaluate("window.__railfanVisualRequests?.length||0");
        if(requestsBeforeFollowupSend!==0) throw new Error(`follow-up sent a request instead of filling the input: ${requestsBeforeFollowupSend}`);
        const dateFollowup=await cdp.evaluate("(()=>{const button=[...document.querySelectorAll('.row.assistant.has-table-answer .followups button')].find(node=>node.textContent.trim()==='换个日期');if(!button)return {visible:false};const r=button.getBoundingClientRect();const before=window.__railfanVisualRequests?.length||0;button.click();return {visible:r.width>0&&r.height>0,input:document.querySelector('#input').value,focused:document.activeElement?.id==='input',requestsBefore:before,requestsAfter:window.__railfanVisualRequests?.length||0}})()");
        if(!dateFollowup.visible||!dateFollowup.focused||!dateFollowup.input.endsWith("换个日期查询这趟车的时刻")||dateFollowup.requestsAfter!==dateFollowup.requestsBefore) throw new Error(`date follow-up did not enter editing state: ${JSON.stringify(dateFollowup)}`);
        log.push({state:name,interaction:"date follow-up fills draft without request",result:dateFollowup});
        await cdp.evaluate("document.querySelector('#input').value='继续追问阅读测试';document.querySelector('#input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#send').click()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequestState==='open'"),"reading stream");
        await cdp.evaluate("const c=document.querySelector('#chat');c.scrollTop=Math.max(0,c.scrollHeight-c.clientHeight-120);c.dispatchEvent(new Event('scroll'))");
        await delay(100);
        const anchorBefore=await cdp.evaluate("(()=>{const c=document.querySelector('#chat'),r=c.getBoundingClientRect(),e=[...c.children].find(n=>{const b=n.getBoundingClientRect();return b.height>0&&b.bottom>r.top&&b.top<r.bottom});window.__readingAnchor=e;return {text:e?.innerText?.slice(0,50),top:e?.getBoundingClientRect().top}})()");
        const topBefore=await cdp.evaluate("document.querySelector('#chat').scrollTop");
        await cdp.evaluate("window.__railfanVisualRelease()");
        await waitFor(()=>cdp.evaluate("window.__railfanVisualRequestState==='completed'"),"new stream content");
        const reading=await cdp.evaluate(`(()=>{const e=window.__readingAnchor;return {before:${Number(topBefore)},after:document.querySelector('#chat').scrollTop,anchorText:e?.innerText?.slice(0,50),anchorTop:e?.getBoundingClientRect().top,anchorDeltaPx:e?e.getBoundingClientRect().top-${Number(anchorBefore.top)}:null,cue:!document.querySelector('#new-content-cue').hidden}})()`);
        reading.positionPreserved = reading.anchorDeltaPx != null && Math.abs(reading.anchorDeltaPx)<=2;
        reading.cueVisible = reading.cue;
        await screenshot(cdp,name,{visible:[".md table",".followup","#new-content-cue"]});
        await cdp.evaluate("document.querySelector('#new-content-cue').click()");
        const bottom=await cdp.evaluate("Math.abs(document.querySelector('#chat').scrollHeight-document.querySelector('#chat').clientHeight-document.querySelector('#chat').scrollTop)<3");
        if(!bottom) throw new Error("return-to-bottom did not resume following");
        if(!reading.positionPreserved||!reading.cueVisible) throw new Error(`visible reading anchor shifted: ${JSON.stringify({anchorBefore,reading})}`);
        log.push({state:name,interaction:"scroll pause/follow-up/return to bottom",result:{reading,bottom,draftPreserved:true,followupDidNotSend:requestsBeforeFollowupSend===0}});
      } else if(name==="query_details"){
        await cdp.evaluate("document.querySelector('.schedule-detail-label').click()");
        await waitSelector(cdp,".request-details",true);
        await cdp.evaluate("document.querySelector('.request-details').scrollIntoView({block:'start'})");
        await screenshot(cdp,name,{visible:[".request-details"],preserveChatScroll:true});
        const detailText=await cdp.evaluate("document.querySelector('.request-details').innerText");
        const meta=fixture.states.query_details;
        const expected=[meta.source,meta.date,meta.time_basis,"5.8",Number(meta.usage.total_tokens).toLocaleString("en-US")];
        if(expected.some(value=>!detailText.includes(value))) throw new Error(`request details metadata mismatch: ${JSON.stringify({expected,detailText})}`);
        log.push({state:name,interaction:"request details metadata",result:{expected,fromFixture:true,visible:true}});
      } else if(name==="history"){
        await waitSelector(cdp,".history-item",true);
        await cdp.evaluate("document.querySelector('.history-more').click()");
        await waitSelector(cdp,".history-action-panel",true);
        const panel=await cdp.evaluate("document.querySelector('.history-action-panel').innerText");
        if(!panel.includes("重命名")||!panel.includes("删除对话")) throw new Error(`history actions missing: ${panel}`);
        await screenshot(cdp,name,{visible:[".history-action-panel"],target:{".history-search":{x:17,y:96,width:357,height:41},".history-create":{x:17,y:145,width:357,height:44}}});
        await cdp.evaluate("document.querySelector('.history-action-cancel').click()");
        const cancelled=await cdp.evaluate("!document.querySelector('.history-action-panel')");
        if(!cancelled) throw new Error("history cancel did not close action panel");
        await cdp.evaluate("document.querySelectorAll('.history-select')[1].click()");
        await waitFor(()=>cdp.evaluate("location.hash.startsWith('#/c/')"),"history selection");
        const selected=await cdp.evaluate("location.hash");
        await cdp.evaluate("document.querySelector('#history-btn').click()");
        await waitSelector(cdp,".history-item",true);
        await cdp.evaluate("(()=>{const s=document.querySelector('.history-search');s.value='G8932';s.dispatchEvent(new Event('input',{bubbles:true}))})()");
        const searchCount=await cdp.evaluate("document.querySelectorAll('.history-item').length");
        if(searchCount!==1) throw new Error(`history search returned ${searchCount} items`);
        await cdp.evaluate("(()=>{const s=document.querySelector('.history-search');s.value='';s.dispatchEvent(new Event('input',{bubbles:true}))})()");
        const groupLabels=await cdp.evaluate("[...document.querySelectorAll('.history-group-title')].map(x=>x.textContent)");
        if(!groupLabels.includes("今天")||!groupLabels.includes("昨天")) throw new Error(`history date groups missing: ${groupLabels}`);
        await cdp.evaluate("document.querySelector('.history-create').click()");
        await waitFor(()=>cdp.evaluate("location.hash.startsWith('#/c/')"),"new conversation action");
        const newId=await cdp.evaluate("location.hash.slice(4)");
        await cdp.evaluate("document.querySelector('#history-btn').click()");
        await waitSelector(cdp,".history-item",true);
        await cdp.evaluate("(()=>{const item=[...document.querySelectorAll('.history-item')].find(x=>x.textContent.includes('G8932 列车时刻'));item.querySelector('.history-more').click()})()");
        await waitSelector(cdp,".history-action-panel",true);
        await cdp.evaluate("document.querySelector('.history-rename').click()");
        await waitSelector(cdp,".modal input",true);
        await cdp.evaluate("const i=document.querySelector('.modal input');i.value='验收重命名';document.querySelector('.modal button.primary').click()");
        await waitFor(()=>cdp.evaluate("[...document.querySelectorAll('.history-item strong')].some(x=>x.textContent==='验收重命名')"),"conversation rename");
        const renamed=await cdp.evaluate("[...JSON.parse(localStorage.getItem('railfan_conversations_v1'))].find(x=>x.id==='history_today2')?.title");
        await cdp.evaluate("(()=>{const item=[...document.querySelectorAll('.history-item')].find(x=>x.textContent.includes('验收重命名'));item.querySelector('.history-more').click()})()");
        await waitSelector(cdp,".history-action-panel",true);
        await cdp.evaluate("document.querySelector('.history-action-panel .danger').click()");
        await waitSelector(cdp,".modal",true);
        await cdp.evaluate("document.querySelector('.modal button.danger').click()");
        await waitFor(()=>cdp.evaluate("!JSON.parse(localStorage.getItem('railfan_conversations_v1')).some(x=>x.id==='history_today2')"),"conversation delete");
        log.push({state:name,interaction:"search/date groups/select/new/rename/delete/cancel",result:{cancelled,selected,searchCount,groupLabels,newId,renamed,deleted:true}});
      }
    }
    await fs.writeFile(path.join(outputDir,"verification.json"),JSON.stringify({browser:"Microsoft Edge",viewport,results:log},null,2)+"\n");
    console.log(JSON.stringify({captured:names.length,outputDir,viewport,interactions:log.length},null,2));
  } finally {
    cdp?.close();
    browser.kill("SIGTERM"); server.kill("SIGTERM");
    await Promise.all([new Promise(resolve=>browser.once("exit",resolve)),new Promise(resolve=>server.once("exit",resolve))]);
    await fs.rm(profile,{recursive:true,force:true});
  }
}

main().catch(error=>{console.error(`FAIL: ${error.stack||error}`);process.exitCode=1;});

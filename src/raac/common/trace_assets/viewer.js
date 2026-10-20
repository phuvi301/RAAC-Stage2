"use strict";

import {indexUsage, tokenLabel, summarizeCache} from "/usage.mjs";

// All log content is inserted as text, including code and nested JSON strings.
const $ = id => document.getElementById(id);
const colors = {analyst:"#6186b9",planner:"#9170ae",coder:"#248e83",tester:"#c18a38",reviewer:"#a46f82",mas:"#6f8792"};
const names = {analyst:"Analyst",planner:"Planner",coder:"Coder",tester:"Tester",reviewer:"Reviewer",mas:"MAS"};
const labels = {requirement:"Yêu cầu",incoming_message:"Bàn giao đầu vào",artifacts:"Tài liệu và kết quả",spec:"Đặc tả",plan:"Kế hoạch",summary:"Tóm tắt",acceptance_criteria:"Tiêu chí nghiệm thu",constraints:"Ràng buộc",approach:"Phương án",steps:"Các bước",risks:"Rủi ro",content:"Nội dung",messages:"Messages",role:"Vai trò",tool_calls:"Yêu cầu tool",arguments:"Tham số",result:"Kết quả",execution:"Thực thi",stdout:"stdout",stderr:"stderr",tests_run:"Số test",failed:"Test lỗi",status:"Trạng thái",verdict:"Phán quyết",notes:"Ghi chú",patch:"Patch",test_cases:"Các test case",code:"Mã lỗi",error:"Lỗi",error_kind:"Loại lỗi",path:"Đường dẫn",command:"Lệnh",repository_files:"Danh sách file",repository_changes:"File thay đổi",from:"Bên gửi",output_schema:"Cấu trúc kết quả yêu cầu"};
const state = {events:[], ids:new Set(), cards:new Map(), usageSlots:new Map(), cursor:0, paused:false, role:"", query:"", initial:true, warnings:new Set(), completed:false};
const number = value => Number(value || 0).toLocaleString("en-US");
const normalize = value => String(value).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g,"d");
function node(tag, className, text) {const el=document.createElement(tag); if(className) el.className=className; if(text!==undefined) el.textContent=String(text); return el;}
function parsed(value) {if(typeof value==="string" && /^[\[{]/.test(value.trim())) {try{return JSON.parse(value);}catch{}} return value;}
function dataView(raw, depth=0) {
  const value=parsed(raw);
  if(depth>10 || typeof value!=="object" || value===null) {
    const text=typeof value==="object" ? JSON.stringify(value,null,2) : String(value ?? "—");
    return node(text.includes("\n") || text.length>180 ? "pre":"span", text.includes("\n") || text.length>180 ? "data-string":"data-scalar",text);
  }
  if(Array.isArray(value)) {
    const list=node("ol","data-list");
    for(const item of value) {const li=node("li"); li.append(dataView(item,depth+1));list.append(li);}
    if(!value.length) list.append(node("span","data-scalar","[]"));
    return list;
  }
  const dl=node("dl","data-object");
  for(const [key,item] of Object.entries(value)) {
    const dt=node("dt","",labels[key]||key);dt.title=key;const dd=node("dd");
    const nested=parsed(item);
    if(nested!==null && typeof nested==="object" && JSON.stringify(nested).length>450) {
      const details=node("details","data-nested");
      details.append(node("summary","",Array.isArray(nested) ? `${nested.length} mục` : `${Object.keys(nested).length} trường`));
      details.addEventListener("toggle",()=>{if(details.open && !details.dataset.loaded){details.append(dataView(nested,depth+1));details.dataset.loaded="true";}});
      if(depth===0 && ["result","messages"].includes(key))details.open=true;
      dd.append(details);
    } else dd.append(dataView(item,depth+1));
    dl.append(dt,dd);
  }
  return dl;
}
function clock(value) {const date=new Date(value);return value && !Number.isNaN(date.valueOf()) ? date.toLocaleTimeString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh",hour12:false}):"--:--:--";}
function makeCard(event) {
  const card=node("article",`exchange ${event.tone}${event.technical?" technical":""}`);card.id=`event-${event.id}`;
  card.style.setProperty("--accent",colors[event.agent]||colors.mas);
  const head=node("div","exchange-head"), direction=node("div","direction");
  direction.append(node("span","role-dot"),node("span","",event.source),node("span","arrow","→"),node("span","",event.target));
  head.append(direction,node("time","time",clock(event.timestamp)));card.append(head);
  const title=node("div","exchange-title",event.title);
  if(event.tone==="error") title.append(node("span","tag tone-error","LỖI / KHÔNG ĐẠT"));
  else if(event.event==="tool_call_completed") title.append(node("span","tag tone-success","THÀNH CÔNG"));
  if(event.round) title.append(node("span","tag",`Vòng ${event.round}`));
  if(event.turn) title.append(node("span","tag",`LLM #${event.turn}`));
  if(event.origin==="adapter_validation") title.append(node("span","tag","Adapter tự chạy"));
  if(event.origin==="batch_item") title.append(node("span","tag",`Lô · mục ${event.batch_index}/${event.batch_size}`));
  card.append(title);
  if(event.llm_call_id || event.event==="llm_call" ||
      (event.agent_call_id && ["agent_started","agent_completed","agent_failed"].includes(event.event))) {
    const slot=node("div","communication-usage");
    state.usageSlots.set(event.id,slot);card.append(slot);
  }
  if(event.summary) card.append(node("p","exchange-summary",event.summary));
  const meta=node("div","exchange-meta");
  if(event.tool_call_id) {
    const link=node("button","call-link",`tool · ${String(event.tool_call_id).slice(0,24)}`);link.type="button";
    link.title="Đi tới yêu cầu hoặc phản hồi cùng mã tool";
    link.addEventListener("click",()=>{
      const other=state.events.find(item=>item.id!==event.id && item.tool_call_id===event.tool_call_id);
      if(!other)return;
      state.role="";$("errors-only").checked=false;$("search").value="";state.query="";
      if(other.technical)$("technical").checked=true;
      updateFilters();const target=state.cards.get(other.id);target.scrollIntoView({behavior:"smooth",block:"center"});target.classList.add("highlight");setTimeout(()=>target.classList.remove("highlight"),1800);
    });meta.append(link);
  }
  if(event.llm_call_id)meta.append(node("span","",`call · ${String(event.llm_call_id).slice(0,14)}`));
  if(event.latency_ms!==undefined && event.latency_ms!==null)meta.append(node("span","",`${event.latency_ms} ms`));
  if(meta.childElementCount)card.append(meta);
  if(event.payloads.length) {
    const details=node("details");details.append(node("summary","","Mở nội dung"));
    details.addEventListener("toggle",()=>{
      if(!details.open || details.dataset.loaded)return;
      for(const payload of event.payloads){const section=node("section","payload");section.append(node("h3","",payload.label),dataView(payload.data));details.append(section);}
      details.dataset.loaded="true";
    });card.append(details);
  }
  return card;
}
function updateCommunicationUsage() {
  const usage=indexUsage(state.events);
  for(const event of state.events) {
    const slot=state.usageSlots.get(event.id);if(!slot)continue;
    const boundary=["agent_started","agent_completed","agent_failed"].includes(event.event);
    const totals=event.event==="llm_call"?usage.byEvent.get(event.id):
      boundary?usage.byAgent.get(event.agent_call_id):usage.byCall.get(event.llm_call_id);
    const tool=event.event.startsWith("tool_");
    const context=boundary?"Tổng lượt role":tool?"LLM liên quan":event.event==="llm_call"?"HTTP attempt này":"Lượt LLM này";
    slot.replaceChildren(node("span","usage-context",context),
      node("span","usage-input",`Token vào: ${tokenLabel(totals,"input")}`),
      node("span","usage-output",`Token ra: ${tokenLabel(totals,"output")}`),
      node("span","usage-cached",`Cache: ${tokenLabel(totals,"cached")}`));
    if(!totals || totals.input===null || totals.output===null || !totals.inputComplete || !totals.outputComplete)
      slot.append(node("span","usage-pending","Chưa có usage đầy đủ trong log"));
    if(totals?.attempts>1 && !boundary)slot.append(node("span","usage-attempts",`${totals.attempts} HTTP attempt`));
    slot.title=tool?"Token của lượt LLM đã yêu cầu tool này, không phải chi phí token riêng của tool. Các card cùng call ID hiển thị cùng usage; không cộng lại các card.":
      boundary?"Tổng usage API của các lần gọi trong đúng lượt role này. Đây không phải một lần gọi LLM bổ sung.":
      "Usage API được ghép theo call ID; request và response cùng lượt hiển thị cùng chi phí, không cộng hai lần.";
  }
}
function roleButtons() {
  const counts=new Map();for(const event of state.events)counts.set(event.agent,(counts.get(event.agent)||0)+1);
  const container=$("roles");container.replaceChildren();
  for(const [role,count] of [["",state.events.length],...counts]) {
    const button=node("button",`role-button${state.role===role?" selected":""}`);button.type="button";button.setAttribute("aria-pressed",String(state.role===role));button.style.setProperty("--accent",colors[role]||colors.mas);
    const label=role ? names[role]||role : "Tất cả";
    button.append(node("span","role-dot"),node("span","",label),node("small","",number(count)));
    button.title=`${label}: ${number(count)} sự kiện, bao gồm context và HTTP`;
    button.addEventListener("click",()=>{state.role=role;updateFilters();});container.append(button);
  }
}
function updateFilters() {
  let count=0;
  for(const event of state.events) {
    const visible=(!state.role || event.agent===state.role) && (!$("errors-only").checked || event.tone==="error") &&
      ($("technical").checked || !event.technical) && (!state.query || event.search.includes(state.query));
    state.cards.get(event.id).hidden=!visible;if(visible)count++;
  }
  $("visible-count").textContent=`${number(count)} / ${number(state.events.length)} trao đổi`;
  $("empty").hidden=count!==0;roleButtons();
}
function updateStats() {
  let calls=0,input=0,output=0,unknown=false,agents=0,errors=0,budget=null,start=null,end=null;
  for(const event of state.events) {
    if(event.run_id)$("run-id").textContent=event.run_id;
    if(Number.isInteger(event.token_limit))budget=event.token_limit;
    if(event.event==="run_started")start=event;
    if(event.event==="agent_started")agents++;
    if(["tool_call_completed","tool_call_failed","tool_arguments_invalid"].includes(event.event)&&event.tone==="error")errors++;
    if(event.event==="llm_call") {
      calls++;const usage=event.usage||{};
      if(Number.isInteger(usage.prompt_tokens)&&Number.isInteger(usage.completion_tokens)){input+=usage.prompt_tokens;output+=usage.completion_tokens;}else unknown=true;
    }
    if(event.event==="run_completed")end=event;
  }
  let total=input+output;
  if(end && Number.isInteger(end.usage.total_tokens)) {
    total=end.usage.total_tokens;if(Number.isInteger(end.usage.calls))calls=end.usage.calls;
    unknown=end.usage.usage_complete===false;
    if(Number.isInteger(end.usage.prompt_tokens))input=end.usage.prompt_tokens;
    if(Number.isInteger(end.usage.completion_tokens))output=end.usage.completion_tokens;
  }
  $("stat-agents").textContent=number(agents);$("stat-calls").textContent=number(calls);$("stat-errors").textContent=number(errors);
  $("stat-tokens").textContent=(unknown?"≥ ":"")+number(total);
  const cached=summarizeCache(state.events);
  $("stat-token-split").textContent=`Token vào / ra: ${number(input)} / ${number(output)} · Cache: ${cached===null?"chưa có số liệu":number(cached)}`;
  $("stat-budget").textContent=budget?`Ngân sách run: ${number(budget)}`:"Theo usage API đã ghi";
  if(end) {
    $("run-status").textContent=end.status==="completed"?"HOÀN TẤT":"ĐÃ DỪNG";
    $("run-reason").textContent=end.summary;$("run-banner").classList.toggle("error",end.tone==="error");state.completed=true;
  } else {
    $("run-status").textContent=start?"ĐANG CHẠY / CHƯA CÓ KẾT THÚC":"LOG CHƯA ĐỦ TRẠNG THÁI";
    $("run-reason").textContent=start?start.summary:"Chưa thấy sự kiện bắt đầu hoặc kết thúc run.";
    $("run-banner").classList.remove("error");state.completed=false;
  }
}
async function poll() {
  if(state.paused){$("connection").textContent="Đã tạm dừng";setTimeout(poll,1200);return;}
  let delay=1500;
  try {
    const response=await fetch(`/api/events?cursor=${state.cursor}`,{cache:"no-store"});if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const data=await response.json();
    let changed=Boolean(data.reset)||state.initial;
    if(data.reset){state.events=[];state.ids.clear();state.cards.clear();state.usageSlots.clear();$("feed").replaceChildren();}
    if(state.initial){state.role=String(data.initial_agent||"").toLowerCase();state.initial=false;}
    for(const event of data.events) {
      if(state.ids.has(event.id))continue;
      changed=true;
      event.search=normalize([event.source,event.target,event.title,event.summary,event.tool_call_id||"",JSON.stringify(event.payloads)].join(" "));
      state.events.push(event);state.ids.add(event.id);const card=makeCard(event);state.cards.set(event.id,card);$("feed").append(card);
    }
    state.cursor=data.cursor;$("log-name").textContent=data.log_name;
    for(const warning of data.warnings)state.warnings.add(warning);
    $("warning").textContent=[...state.warnings].join(" ");$("warning").hidden=!state.warnings.size;
    if(changed){updateStats();updateCommunicationUsage();updateFilters();}
    $("connection").textContent=data.has_more?"Đang đọc thêm…":state.completed?"Đã đọc đến cuối run":"Đang theo dõi · 1,5 giây/lần";
    delay=data.has_more?20:state.completed?4000:1500;
  } catch(error) {$("connection").textContent=`Mất kết nối · ${error.message}. Đang thử lại…`;delay=3000;}
  setTimeout(poll,delay);
}
$("search").addEventListener("input",event=>{state.query=normalize(event.target.value);updateFilters();});
$("errors-only").addEventListener("change",updateFilters);$("technical").addEventListener("change",updateFilters);
$("pause").addEventListener("click",()=>{state.paused=!state.paused;$("pause").textContent=state.paused?"Tiếp tục cập nhật":"Tạm dừng cập nhật";});
poll();

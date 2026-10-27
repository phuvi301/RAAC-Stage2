"use strict";

import {indexUsage, tokenLabel, summarizeCache} from "/usage.mjs";
import {layout, summarize, STATUS_LABELS} from "/graph.mjs";
import {memorySummary, memoryEntryTitle, mergeMemoryPage, memoryRevisionLabel, memoryChangeSummary, mergeMemoryHistory} from "/memory.mjs";

// All log content is inserted as text, including code and nested JSON strings.
const $ = id => document.getElementById(id);
const colors = {environment:"#477b9e",analyst:"#6186b9",planner:"#9170ae",coder:"#248e83",tester:"#c18a38",reviewer:"#a46f82",mas:"#6f8792"};
const names = {environment:"Environment",analyst:"Analyst",planner:"Planner",coder:"Coder",tester:"Tester",reviewer:"Reviewer",mas:"MAS"};
const labels = {requirement:"Yêu cầu",incoming_message:"Bàn giao đầu vào",artifacts:"Tài liệu và kết quả",spec:"Đặc tả",plan:"Kế hoạch",summary:"Tóm tắt",acceptance_criteria:"Tiêu chí nghiệm thu",constraints:"Ràng buộc",approach:"Phương án",steps:"Các bước",risks:"Rủi ro",content:"Nội dung",messages:"Messages",role:"Vai trò",tool_calls:"Yêu cầu tool",arguments:"Tham số",result:"Kết quả",execution:"Thực thi",stdout:"stdout",stderr:"stderr",tests_run:"Số test",failed:"Test lỗi",status:"Trạng thái",verdict:"Phán quyết",notes:"Ghi chú",patch:"Patch",test_cases:"Các test case",code:"Mã lỗi",error:"Lỗi",error_kind:"Loại lỗi",path:"Đường dẫn",command:"Lệnh",repository_files:"Danh sách file",repository_changes:"File thay đổi",from:"Bên gửi",output_schema:"Cấu trúc kết quả yêu cầu",role_memory:"Memory các vòng trước",entries:"Các bản ghi",identity:"Phạm vi memory",revision:"Revision",compactions:"Số lần compact",estimated_tokens:"Token ước lượng",kind:"Loại bản ghi",round:"Vòng",sequence:"Thứ tự",observation:"Quan sát thực tế",tool:"Tool",feedback:"Phản hồi",preview:"Nội dung rút gọn",tool_counts:"Số lần dùng tool",changed_files:"File đã thay đổi",working_notes:"Ghi chú làm việc",conclusions:"Kết luận",ruled_out:"Giả thuyết đã loại",next_steps:"Bước tiếp theo",statement:"Kết luận",evidence:"Bằng chứng tham chiếu",hypothesis:"Giả thuyết",reason:"Lý do",detail_ref:"Chi tiết trong journal"};
const state = {events:[], ids:new Set(), cards:new Map(), usageSlots:new Map(), cursor:0, paused:false, role:"", query:"", initial:true, warnings:new Set(), completed:false};
const flow = {topology:null, name:"", selected:null, loading:false};
const memoryState = {loaded:false, busy:false, id:"", revision:null, journal:null, journalBusy:false, history:null, historyBusy:false, changeRequest:0};
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
const SVG = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  if (text !== undefined) el.textContent = String(text);
  return el;
}
function reveal(event) {
  state.role = ""; $("errors-only").checked = false; $("search").value = ""; state.query = "";
  if (event.technical) $("technical").checked = true;
  updateFilters();
  const target = state.cards.get(event.id);
  if (!target) return;
  target.scrollIntoView({behavior:"smooth", block:"center"});
  target.classList.add("highlight"); setTimeout(() => target.classList.remove("highlight"), 1800);
}
function clip(item, toward, margin = 3) {
  // Where the line from this node's centre toward another leaves its outline (circle or rectangle).
  const dx = toward.x - item.x, dy = toward.y - item.y, length = Math.hypot(dx, dy) || 1;
  const ux = dx / length, uy = dy / length;
  let reach;
  if (item.control && item.node_id !== "result") reach = 22;
  else {
    const hw = item.node_id === "result" ? 36 : 59, hh = item.node_id === "result" ? 18 : 25;
    reach = Math.min(Math.abs(ux) > 1e-9 ? hw / Math.abs(ux) : Infinity, Math.abs(uy) > 1e-9 ? hh / Math.abs(uy) : Infinity);
  }
  return {x: item.x + ux * (reach + margin), y: item.y + uy * (reach + margin)};
}
function followedNode(summary) {
  const nodes = [...summary.nodes.values()].filter(item => !item.control);
  return (nodes.find(item => item.status === "running") || [...nodes].reverse().find(item => item.runs > 0) || nodes[0])?.node_id;
}
function renderFlowDetail(summary, id) {
  const panel = $("flow-detail"), info = summary.nodes.get(id);
  panel.replaceChildren();
  if (!info) { panel.append(node("p", "notice", "Chọn một node trong đồ thị.")); return; }
  const head = node("div", "flow-detail-head");
  head.append(node("h3", "", info.control ? (id === "result" ? "Kết quả chain" : id === "check" ? "Check · kiểm tra test" : "Limit · giới hạn vòng") : names[info.agent] || info.role),
    node("span", `status-pill flow-status ${info.status}`, STATUS_LABELS[info.status]));
  panel.append(head);
  const manual = flow.selected !== null;
  const mode = node("p", "flow-follow", manual ? "Đang xem node bạn chọn. " : "Tự theo node đang chạy. ");
  if (manual) { const button = node("button", "link-button", "Theo node đang chạy"); button.type = "button"; button.addEventListener("click", () => {flow.selected = null; renderFlow();}); mode.append(button); }
  panel.append(mode);
  if (info.control) {
    panel.append(node("p", "flow-activity", info.note));
    if (info.handoff) panel.append(node("p", "exchange-summary", info.handoff));
    if (info.error) panel.append(node("p", "memory-warning", info.error));
    return;
  }
  const doing = node("div", "flow-activity");
  doing.append(node("span", "flow-label", "ĐANG LÀM GÌ"));
  if (info.activity) {
    doing.append(node("strong", "", info.activity.title));
    if (info.activity.detail) doing.append(node("span", "flow-detail-text", info.activity.detail));
  } else if (info.status === "idle") doing.append(node("strong", "", "Chưa tới lượt, đang chờ bước trước bàn giao."));
  else if (info.status === "done") doing.append(node("strong", "", "Đã xong lượt này và bàn giao kết quả."));
  else if (info.status === "failed") doing.append(node("strong", "", "Lượt chạy kết thúc với lỗi hoặc không đạt."));
  else doing.append(node("strong", "", "Run đã kết thúc khi role chưa trả kết quả."));
  panel.append(doing);
  if (info.handoff) { const block = node("div", "flow-activity"); block.append(node("span", "flow-label", "BÀN GIAO GẦN NHẤT"), node("span", "flow-detail-text", info.handoff)); panel.append(block); }
  if (info.error) panel.append(node("p", "memory-warning", info.error));
  const metrics = node("div", "flow-metrics");
  const total = info.tokens.complete ? number(info.tokens.input + info.tokens.output) : "≥ " + number(info.tokens.input + info.tokens.output);
  for (const [label, value] of [["Lượt chạy", info.runs ? `${info.runs}${info.round ? ` · vòng ${info.round}` : ""}` : "0"], ["Gọi LLM", number(info.llmCalls)],
      ["Gọi tool", `${number(info.toolCalls)}${info.toolErrors ? ` (${info.toolErrors} lỗi)` : ""}`], ["Token vào/ra", `${number(info.tokens.input)} / ${number(info.tokens.output)}`], ["Tổng token", total]]) {
    const cell = node("div"); cell.append(node("small", "", label), node("strong", "", value)); metrics.append(cell);
  }
  panel.append(metrics);
  const actions = node("div", "flow-actions");
  const filter = node("button", "", "Lọc luồng theo role này"); filter.type = "button";
  filter.addEventListener("click", () => {state.role = info.agent; updateFilters(); $("feed").scrollIntoView({behavior:"smooth"});});
  actions.append(filter); panel.append(actions);
  if (info.recent.length) {
    panel.append(node("span", "flow-label", "HOẠT ĐỘNG GẦN ĐÂY"));
    const list = node("ol", "flow-timeline");
    for (const event of info.recent) {
      const item = node("li", event.tone), button = node("button", "timeline-item"); button.type = "button";
      button.append(node("time", "", clock(event.timestamp)), node("strong", "", event.title));
      if (event.summary) button.append(node("span", "", event.summary.length > 140 ? event.summary.slice(0, 137) + "…" : event.summary));
      button.title = "Đi tới sự kiện trong luồng trao đổi"; button.addEventListener("click", () => reveal(event));
      item.append(button); list.append(item);
    }
    panel.append(list);
  }
}
function edgeLabel(edge) {
  const start = state.events.find(event => event.event === "run_started") || {};
  if (edge.from === "reviewer" && edge.to === "coder")
    return Number.isInteger(start.max_review_rounds) ? `Reviewer từ chối · tối đa ${start.max_review_rounds} vòng` : "Reviewer từ chối";
  if (edge.from === "limit" && edge.to === "coder")
    return Number.isInteger(start.max_rounds) ? `Test chưa đạt · tối đa ${start.max_rounds} vòng` : "Test chưa đạt";
  return null;
}
function renderFlow() {
  if (!flow.topology) return;
  const summary = summarize(flow.topology, state.events), shape = layout(flow.topology);
  const current = flow.selected && summary.nodes.has(flow.selected) ? flow.selected : followedNode(summary);
  const canvas = svg("svg", {viewBox: `0 0 ${shape.width} ${shape.height}`, role: "group", "aria-label": "Đồ thị topology"});
  const defs = svg("defs");
  for (const [id, fill] of [["arrow", "#a7b4ba"], ["arrow-on", "#13786e"], ["arrow-live", "#0f8d80"]]) {
    const marker = svg("marker", {id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse"});
    marker.append(svg("path", {d: "M0 0L10 5L0 10z", fill})); defs.append(marker);
  }
  canvas.append(defs);
  const at = new Map(shape.nodes.map(item => [item.node_id, item]));
  for (const edge of summary.edges) {
    const from = at.get(edge.from), to = at.get(edge.to); if (!from || !to) continue;
    let d, label = null;
    if (edge.back) {
      const key = `${edge.from}>${edge.to}`;
      if (shape.topArcs.has(key)) {
        const sx = from.x, tx = to.x, y0 = from.y - 25, y1 = to.y - 27, peak = y0 - 58;
        d = `M${sx} ${y0} C${sx} ${peak} ${tx} ${peak} ${tx} ${y1}`;
        label = {x: (sx + tx) / 2, y: y0 - 47};
      } else {
        const sx = from.x, sy = from.y + 24, tx = to.x, ty = to.y + 27;
        d = `M${sx} ${sy} C${sx} ${sy + 46} ${tx} ${ty + 84} ${tx} ${ty}`;
        label = {x: (sx + tx) / 2, y: (sy + 3 * (sy + 46) + 3 * (ty + 84) + ty) / 8 + 12};
      }
      const text = edgeLabel(edge);
      if (!text) label = null; else label.text = text;
    } else {
      const a = clip(from, to), b = clip(to, from, 5);
      if (flow.topology.layout === "star") {
        // Hub and spoke exchange messages both ways: draw the two directions side by side.
        const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy) || 1, offset = (edge.from < edge.to ? 1 : -1) * 5;
        const ox = -dy / length * offset, oy = dx / length * offset;
        d = `M${a.x + ox} ${a.y + oy} L${b.x + ox} ${b.y + oy}`;
      } else d = `M${a.x} ${a.y} L${b.x} ${b.y}`;
    }
    const cls = `flow-edge${edge.traversed ? " traversed" : ""}${edge.active ? " active" : ""}`;
    canvas.append(svg("path", {d, class: cls, "marker-end": `url(#${edge.active ? "arrow-live" : edge.traversed ? "arrow-on" : "arrow"})`}));
    if (label) canvas.append(svg("text", {class: `flow-edge-label${edge.traversed ? " traversed" : ""}`, x: label.x, y: label.y}, label.text));
  }
  for (const item of shape.nodes) {
    const info = summary.nodes.get(item.node_id);
    const group = svg("g", {class: `flow-node ${info.status}${item.control ? " control" : ""}${item.node_id === current ? " selected" : ""}`,
      transform: `translate(${item.x} ${item.y})`, tabindex: 0, role: "button",
      "aria-label": `${item.control ? item.node_id : names[item.agent] || item.role}: ${STATUS_LABELS[info.status]}`, "aria-pressed": String(item.node_id === current)});
    if (item.control) {
      const r = item.node_id === "result" ? 28 : 22;
      group.append(item.node_id === "result" ? svg("rect", {x: -r - 8, y: -18, width: (r + 8) * 2, height: 36, rx: 18}) : svg("circle", {r}));
      group.append(svg("text", {class: "flow-name", y: 4}, item.node_id === "result" ? "Kết quả" : item.node_id === "check" ? "Check" : "Limit"));
    } else {
      group.style.setProperty("--accent", colors[item.agent] || colors.mas);
      group.append(svg("rect", {x: -59, y: -25, width: 118, height: 50, rx: 10}), svg("rect", {class: "flow-accent", x: -59, y: -25, width: 5, height: 50, rx: 2}));
      group.append(svg("text", {class: "flow-name", y: -2}, names[item.agent] || item.role), svg("text", {class: "flow-state", y: 14}, STATUS_LABELS[info.status]));
      if (info.runs > 1) group.append(svg("text", {class: "flow-runs", x: 52, y: -29}, `×${info.runs}`));
    }
    const select = () => {flow.selected = item.node_id; renderFlow();};
    group.addEventListener("click", select);
    group.addEventListener("keydown", event => {if (event.key === "Enter" || event.key === " ") {event.preventDefault(); select();}});
    canvas.append(group);
  }
  $("flow-canvas").replaceChildren(canvas);
  $("flow-topology").textContent = `${flow.topology.topology_id} · v${flow.topology.version}`;
  renderFlowDetail(summary, current);
}
async function loadTopology(name = "chain") {
  if (flow.loading || flow.name === name) return;
  flow.loading = true;
  try {
    const response = await fetch(`/api/topology?name=${encodeURIComponent(name)}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    flow.topology = await response.json(); flow.name = name; flow.selected = null; renderFlow();
  } catch (error) {
    $("flow-topology").textContent = "Chưa tải được topology";
    $("flow-detail").replaceChildren(node("p", "notice", `Không đọc được topology: ${error.message}`));
  } finally { flow.loading = false; }
}
function resetMemoryChanges() {
  memoryState.history=null;memoryState.changeRequest++;
  $("memory-revision").replaceChildren();$("memory-change-content").replaceChildren();
  $("memory-change-status").textContent="";$("memory-history-more").hidden=true;
}
async function loadMemoryChange() {
  const id=memoryState.id, revision=$("memory-revision").value, request=++memoryState.changeRequest;
  if(!id || !revision)return;
  $("memory-change-status").textContent="Đang đọc thay đổi…";$("memory-change-content").replaceChildren();
  try {
    const response=await fetch(`/api/memory/change?id=${encodeURIComponent(id)}&revision=${revision}`,{cache:"no-store"});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const change=await response.json();if(memoryState.id!==id || request!==memoryState.changeRequest)return;
    $("memory-change-status").textContent=memoryChangeSummary(change);
    const target=$("memory-change-content");
    if(change.metadata?.timestamp)target.append(node("p","memory-file",`Lưu lúc ${clock(change.metadata.timestamp)} · giờ Việt Nam`));
    if(change.available) {
      const before=change.before,after=change.after;
      target.append(node("p","memory-size",`${before.entries} → ${after.entries} mục trong bộ nhớ · token ước lượng ${before.estimated_tokens ?? "?"} → ${after.estimated_tokens ?? "?"}`));
      if(change.metadata?.compacted)target.append(node("p","memory-compacted",`Compact sau khi thêm bản ghi: ~${change.metadata.before_estimated_tokens ?? "?"} → ~${change.metadata.after_estimated_tokens ?? "?"} token. Journal vẫn giữ nội dung gốc.`));
      for(const [field,title,style] of [["added","Được thêm","added"],["removed","Bị bỏ khỏi snapshot","removed"],["updated","Rút gọn / thay đổi","updated"]]) {
        if(!change[field].length)continue;
        target.append(node("h3","",`${title} · ${change[field].length}`));
        for(const item of change[field]) {
          const details=node("details",`memory-entry memory-diff-${style}`),entry=field==="updated"?item.after:item;
          details.append(node("summary","",memoryEntryTitle(entry)));
          details.addEventListener("toggle",()=>{
            if(!details.open || details.dataset.loaded)return;
            if(field==="updated") {
              const columns=node("div","memory-diff-columns");
              for(const [key,label] of [["before","Trước"],["after","Sau"]]){const column=node("div");column.append(node("h4","",label),dataView(item[key]));columns.append(column);}
              details.append(columns);
            } else details.append(dataView(item));
            details.dataset.loaded="true";
          });
          target.append(details);
        }
      }
    }
    for(const warning of change.warnings||[])target.append(node("p","memory-warning",warning));
  } catch(error){if(memoryState.id===id && request===memoryState.changeRequest)$("memory-change-status").textContent=`Chưa đọc được thay đổi: ${error.message}`;}
}
async function loadMemoryHistory(more=false) {
  if(!memoryState.id || memoryState.historyBusy)return;
  const id=memoryState.id,previous=memoryState.history,selected=$("memory-revision").value;
  memoryState.historyBusy=true;$("memory-history-more").disabled=true;
  try {
    const cursor=more && previous?.id===id?previous.cursor:0;
    const response=await fetch(`/api/memory/history?id=${encodeURIComponent(id)}&cursor=${cursor}`,{cache:"no-store"});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const page=await response.json();if(memoryState.id!==id)return;
    const history=mergeMemoryHistory(more?previous:null,page);memoryState.history=history;
    const select=$("memory-revision");select.replaceChildren();
    for(const row of history.revisions){const option=node("option","",memoryRevisionLabel(row));option.value=row.revision;select.append(option);}
    if(history.revisions.some(row=>String(row.revision)===selected))select.value=selected;
    $("memory-history-more").hidden=!history.has_more;
    if(history.revisions.length)await loadMemoryChange();else $("memory-change-status").textContent="Chưa có revision đã lưu.";
    for(const warning of history.warnings||[])$("memory-change-content").append(node("p","memory-warning",warning));
  } catch(error){if(memoryState.id===id)$("memory-change-status").textContent=`Chưa đọc được lịch sử: ${error.message}`;}
  finally {memoryState.historyBusy=false;$("memory-history-more").disabled=false;}
}
function resetMemoryJournal() {
  resetMemoryChanges();
  memoryState.journal=null;
  $("memory-entries").replaceChildren();
  $("memory-journal-count").textContent="";
  $("memory-more").hidden=true;
}
async function loadMemoryJournal() {
  if(!memoryState.id || memoryState.journalBusy)return;
  memoryState.journalBusy=true;$("memory-more").disabled=true;
  const id=memoryState.id, previous=memoryState.journal;
  try {
    const cursor=previous?.id===id?previous.cursor:0;
    const response=await fetch(`/api/memory?id=${encodeURIComponent(id)}&cursor=${cursor}`,{cache:"no-store"});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const page=await response.json();if(memoryState.id!==id)return;
    const before=page.reset?0:previous?.entries.length||0;
    if(page.reset)$("memory-entries").replaceChildren();
    const merged=mergeMemoryPage(previous,page);memoryState.journal=merged;
    for(const entry of merged.entries.slice(before)) {
      const details=node("details","memory-entry");
      details.append(node("summary","",memoryEntryTitle(entry)));
      details.addEventListener("toggle",()=>{if(details.open&&!details.dataset.loaded){details.append(dataView(entry));details.dataset.loaded="true";}});
      $("memory-entries").append(details);
    }
    $("memory-journal-count").textContent=` · ${number(merged.entries.length)} bản ghi đã tải`;
    $("memory-more").hidden=!page.has_more;
    if(page.warnings?.length)$("memory-warning").textContent=page.warnings.join(" ");
  } catch(error) {$("memory-warning").textContent=`Chưa đọc được nhật ký memory: ${error.message}`;}
  finally {memoryState.journalBusy=false;$("memory-more").disabled=false;}
}
async function refreshMemory() {
  if(memoryState.busy)return;memoryState.busy=true;
  try {
    const response=await fetch("/api/memory",{cache:"no-store"});if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const data=await response.json(), memories=data.memories||[];
    memoryState.loaded=true;$("memory-count").textContent=`${memories.length} phạm vi`;
    $("memory-warning").textContent=(data.warnings||[]).join(" ");
    $("memory-empty").hidden=memories.length>0;$("memory-content").hidden=!memories.length;
    if(!memories.length){memoryState.id="";memoryState.revision=null;resetMemoryJournal();return;}
    const selected=memories.find(item=>item.id===memoryState.id)||memories[0];
    if(selected.id!==memoryState.id){memoryState.id=selected.id;memoryState.revision=null;resetMemoryJournal();}
    const select=$("memory-scope");select.replaceChildren();
    for(const saved of memories){const option=node("option","",`${saved.identity?.work_id||saved.id} · ${memorySummary(saved)}`);option.value=saved.id;select.append(option);}
    select.value=memoryState.id;
    $("memory-status").textContent=memorySummary(selected);
    $("memory-file").textContent=selected.snapshot_file||"";
    if(memoryState.revision!==selected.revision){$("memory-snapshot").replaceChildren(dataView(selected));memoryState.revision=selected.revision;}
    $("memory-journal").hidden=!selected.journal_available;
    $("memory-changes-toggle").disabled=!selected.journal_available;
    if(!$("memory-changes").hidden && memoryState.history?.latest_revision!==selected.revision)await loadMemoryHistory();
    if($("memory-journal").open && (!memoryState.journal || !memoryState.journal.has_more))await loadMemoryJournal();
  } catch(error){$("memory-warning").textContent=`Chưa đọc được memory: ${error.message}`;}
  finally{memoryState.busy=false;}
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
    if(changed){
      const declared=[...data.events].reverse().find(event=>event.event==="run_started"&&event.topology);
      if(declared&&declared.topology!==flow.name)loadTopology(declared.topology);
      updateStats();updateCommunicationUsage();updateFilters();renderFlow();
    }
    if(!memoryState.loaded || $("memory-panel").open || data.events.some(event=>event.event.startsWith("memory_")))await refreshMemory();
    $("connection").textContent=data.has_more?"Đang đọc thêm…":state.completed?"Đã đọc đến cuối run":"Đang theo dõi · 1,5 giây/lần";
    delay=data.has_more?20:state.completed?4000:1500;
  } catch(error) {$("connection").textContent=`Mất kết nối · ${error.message}. Đang thử lại…`;delay=3000;}
  setTimeout(poll,delay);
}
$("search").addEventListener("input",event=>{state.query=normalize(event.target.value);updateFilters();});
$("errors-only").addEventListener("change",updateFilters);$("technical").addEventListener("change",updateFilters);
$("pause").addEventListener("click",()=>{state.paused=!state.paused;$("pause").textContent=state.paused?"Tiếp tục cập nhật":"Tạm dừng cập nhật";});
$("memory-panel").addEventListener("toggle",()=>{if($("memory-panel").open)refreshMemory();});
$("memory-journal").addEventListener("toggle",()=>{if($("memory-journal").open)loadMemoryJournal();});
$("memory-more").addEventListener("click",loadMemoryJournal);
$("memory-refresh").addEventListener("click",refreshMemory);
$("memory-changes-toggle").addEventListener("click",()=>{
  const panel=$("memory-changes");panel.hidden=!panel.hidden;
  $("memory-changes-toggle").setAttribute("aria-expanded",String(!panel.hidden));
  $("memory-changes-toggle").textContent=panel.hidden?"Xem thay đổi memory":"Ẩn thay đổi memory";
  if(!panel.hidden)loadMemoryHistory();
});
$("memory-revision").addEventListener("change",loadMemoryChange);
$("memory-history-more").addEventListener("click",()=>loadMemoryHistory(true));
$("memory-scope").addEventListener("change",event=>{memoryState.id=event.target.value;memoryState.revision=null;resetMemoryJournal();refreshMemory();});
loadTopology();
poll();

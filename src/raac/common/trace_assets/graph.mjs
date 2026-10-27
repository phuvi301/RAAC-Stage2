// Topology graph model: layout and per-node progress derived only from logged events.
// Control nodes (check/limit/result) write no agent events; their state is inferred from neighbours.

export const STATUS_LABELS = {idle: "Chưa chạy", running: "Đang chạy", done: "Hoàn tất", failed: "Lỗi / dừng", stopped: "Bị ngắt"};
export const CONTROL_NOTES = {
  check: "Kiểm tra test: đạt, đủ AC, patch hash khớp → Reviewer; chưa đạt → Limit.",
  limit: "Còn lượt Coder–Tester thì quay lại Coder, hết vòng thì sang Reviewer.",
  result: "Kết quả cuối của chain (PASS / REJECT hoặc lý do dừng).",
};

const edgeKey = (from, to) => `${from}>${to}`;
const agentOf = step => String(step.role || "").toLowerCase();

/** Back edges (cycles) found by DFS from the entry node. */
export function backEdges(topology) {
  if (topology.layout === "star") return new Set();  // hub-and-spoke edges are drawn as parallel pairs, not loops
  const out = new Map(topology.steps.map(step => [step.node_id, []]));
  for (const [from, to] of topology.edges) out.get(from)?.push(to);
  const state = new Map(), back = new Set();
  const visit = node => {
    state.set(node, 1);
    for (const next of out.get(node) || []) {
      if (state.get(next) === 1) back.add(edgeKey(node, next));
      else if (!state.has(next)) visit(next);
    }
    state.set(node, 2);
  };
  visit(topology.entry_node);
  return back;
}

/** Hub in the middle, the other roles on a circle around it, entry on the left and exit on the right. */
function starLayout(topology, {marginX, marginY, radiusX = 205, radiusY = 135}) {
  const degree = new Map(topology.steps.map(step => [step.node_id, 0]));
  for (const [from, to] of topology.edges) {degree.set(from, degree.get(from) + 1); degree.set(to, degree.get(to) + 1);}
  const hub = [...degree.entries()].sort((a, b) => b[1] - a[1])[0][0];
  const spokes = topology.steps.map(step => step.node_id).filter(id => ![hub, topology.entry_node, topology.exit_node].includes(id));
  const cx = marginX + radiusX + 150, cy = marginY + radiusY + 24;
  const positions = new Map([[topology.entry_node, {x: marginX, y: cy}], [hub, {x: cx, y: cy}],
    [topology.exit_node, {x: cx + radiusX + 140, y: cy}]]);
  spokes.forEach((id, index) => {
    const angle = (-90 + index * 360 / spokes.length) * Math.PI / 180;
    positions.set(id, {x: cx + radiusX * Math.cos(angle), y: cy + radiusY * Math.sin(angle)});
  });
  const nodes = topology.steps.map(step => ({...step, agent: agentOf(step), control: !step.role,
    ...(positions.get(step.node_id) || {x: cx, y: cy}), row: 0}));
  return {nodes, back: new Set(), topArcs: new Set(),
    width: Math.max(...nodes.map(node => node.x)) + marginX, height: cy + radiusY + marginY};
}

/** Shortest entry→exit path forms the main row; other nodes hang below, between their neighbours. */
export function layout(topology, {stepX = 138, rowGap = 92, marginX = 74, marginY = 64} = {}) {
  if (topology.layout === "star") return starLayout(topology, {marginX, marginY});
  const back = backEdges(topology);
  const forward = topology.edges.filter(([from, to]) => !back.has(edgeKey(from, to)));
  const parent = new Map([[topology.entry_node, null]]);
  const queue = [topology.entry_node];
  while (queue.length) {
    const node = queue.shift();
    for (const [from, to] of forward) if (from === node && !parent.has(to)) {parent.set(to, node); queue.push(to);}
  }
  const main = [];
  for (let node = parent.has(topology.exit_node) ? topology.exit_node : null; node; node = parent.get(node)) main.unshift(node);
  // Back edges leaving the main row arc over the top, so they never share a path with the lower loop-backs.
  const topArcs = new Set([...back].filter(key => main.includes(key.split(">")[0])));
  const baseY = marginY + (topArcs.size ? 40 : 0);
  const positions = new Map(main.map((node, index) => [node, {x: marginX + index * stepX, y: baseY, row: 0}]));
  const pending = topology.steps.map(step => step.node_id).filter(node => !positions.has(node));
  const taken = new Set();
  for (let pass = 0; pass < pending.length + 1; pass++) {
    for (const node of pending) {
      if (positions.has(node)) continue;
      const neighbours = forward.flatMap(([from, to]) => from === node ? [to] : to === node ? [from] : [])
        .filter(other => positions.has(other) && positions.get(other).row === 0);
      if (!neighbours.length) continue;
      const x = neighbours.reduce((sum, other) => sum + positions.get(other).x, 0) / neighbours.length;
      let row = 1;
      while (taken.has(`${Math.round(x)}:${row}`)) row++;
      taken.add(`${Math.round(x)}:${row}`);
      positions.set(node, {x, y: baseY + row * rowGap, row});
    }
  }
  const nodes = topology.steps.map(step => ({...step, agent: agentOf(step), control: !step.role, ...(positions.get(step.node_id) || {x: marginX, y: baseY, row: 0})}));
  const width = Math.max(...nodes.map(node => node.x)) + marginX;
  const height = Math.max(...nodes.map(node => node.y)) + marginY + (back.size ? 34 : 0);
  return {nodes, back, topArcs, width, height};
}

function pendingTool(events) {
  const finished = new Set(events.filter(e => ["tool_call_completed", "tool_call_failed"].includes(e.event)).map(e => e.tool_call_id));
  return [...events].reverse().find(e => e.event === "tool_call_started" && !finished.has(e.tool_call_id));
}

/** What the role is doing right now, from the newest observed protocol events. */
export function currentActivity(events, running) {
  if (!running) return null;
  const tool = pendingTool(events);
  const last = events[events.length - 1];
  if (tool) return {title: `Đang chạy tool · ${tool.tool_name || "?"}`, detail: tool.summary || ""};
  if (last?.event === "llm_request" || last?.event === "llm_attempt_started")
    return {title: `Đang chờ LLM trả lời${last.turn ? ` (lượt #${last.turn})` : ""}`, detail: last.summary || ""};
  if (last?.event === "llm_response") return {title: "Vừa nhận phản hồi từ LLM", detail: last.summary || ""};
  if (last?.event === "tool_call_completed") return {title: `Vừa nhận kết quả · ${last.tool_name || "?"}`, detail: last.summary || ""};
  return {title: last?.title || "Đang xử lý", detail: last?.summary || ""};
}

function agentSummary(step, events, ended) {
  const mine = events.filter(event => event.agent === agentOf(step));
  const lifecycle = mine.filter(event => ["agent_started", "agent_completed", "agent_failed"].includes(event.event));
  const last = lifecycle[lifecycle.length - 1];
  let status = "idle";
  if (last) {
    if (last.event === "agent_started") status = ended ? "stopped" : "running";
    else if (last.event === "agent_failed" || last.tone === "error") status = "failed";
    else status = "done";
  }
  const started = lifecycle.filter(event => event.event === "agent_started");
  const completed = lifecycle.filter(event => event.event === "agent_completed");
  const tokens = {input: 0, output: 0, complete: true};
  for (const event of mine.filter(item => item.event === "llm_call")) {
    const usage = event.usage || {};
    if (Number.isInteger(usage.prompt_tokens) && Number.isInteger(usage.completion_tokens)) {
      tokens.input += usage.prompt_tokens; tokens.output += usage.completion_tokens;
    } else tokens.complete = false;
  }
  // Events of the current/latest turn only, so the activity line reflects this call.
  const lastStart = mine.map(event => event.event).lastIndexOf("agent_started");
  const turn = lastStart >= 0 ? mine.slice(lastStart) : [];
  const failure = [...mine].reverse().find(event => event.event === "agent_failed");
  return {
    node_id: step.node_id, role: step.role, agent: agentOf(step), control: false, status, runs: started.length,
    round: started.length ? started[started.length - 1].round ?? null : null,
    llmCalls: mine.filter(event => event.event === "llm_call").length,
    toolCalls: mine.filter(event => event.event === "tool_call_started" && event.origin !== "adapter_validation").length,
    toolErrors: mine.filter(event => ["tool_call_completed", "tool_call_failed"].includes(event.event) && event.tone === "error").length,
    tokens, activity: currentActivity(turn, status === "running"),
    handoff: completed.length ? completed[completed.length - 1].summary || "" : "",
    error: failure ? failure.summary : status === "failed" && completed.length ? completed[completed.length - 1].summary : "",
    recent: mine.filter(event => !event.technical).slice(-12).reverse(),
  };
}

/** Per-node progress and edge states for the declared topology. */
export function summarize(topology, events) {
  const ended = events.some(event => event.event === "run_completed");
  const end = [...events].reverse().find(event => event.event === "run_completed");
  const nodes = new Map();
  for (const step of topology.steps) if (step.role) nodes.set(step.node_id, agentSummary(step, events, ended));
  const visited = id => id === "result" ? ended : (nodes.get(id)?.runs || 0) > 0;
  for (const step of topology.steps) {
    if (step.role) continue;
    const id = step.node_id;
    let seen = false;
    if (id === "check") seen = (nodes.get("tester")?.status === "done");
    else if (id === "limit") seen = (nodes.get("coder")?.runs || 0) > 1;
    else if (id === "result") seen = ended;
    else seen = topology.edges.some(([from, to]) => to === id && visited(from));
    nodes.set(id, {node_id: id, role: null, agent: "", control: true, status: seen ? "done" : "idle", runs: seen ? 1 : 0,
      activity: null, note: CONTROL_NOTES[id] || "", recent: [], handoff: id === "result" && end ? end.summary || "" : "",
      error: id === "result" && end?.tone === "error" ? end.summary || "" : ""});
  }
  const back = backEdges(topology);
  const reentered = new Set([...back].map(key => key.split(">")[1]));
  const edges = topology.edges.map(([from, to]) => {
    const target = nodes.get(to), source = nodes.get(from);
    const reentry = back.has(edgeKey(from, to));
    const retrying = (target?.runs || 0) > 1 && reentered.has(to);
    const traversed = reentry ? (target?.runs || 0) > 1 : Boolean(visited(to) && visited(from));
    // While a node reruns, only the loop-back edge is live; its first-pass edge is history.
    const active = target?.status === "running" && Boolean(visited(from)) && (reentry ? retrying : !retrying);
    return {from, to, back: reentry, traversed, active};
  });
  return {nodes, edges, ended};
}

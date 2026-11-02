import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";
import {layout, summarize, topologyForRun, STATUS_LABELS} from "../../../src/raac/common/trace_assets/graph.mjs";

const roles = ["Router", "Analyst", "Planner", "Coder", "Tester", "Reviewer"];
const topology = {topology_id:"linear-routed", version:"1", entry_node:"router", exit_node:"finish",
  steps:[...roles.map(role=>({node_id:role.toLowerCase(),role})),{node_id:"finish",role:null}],
  edges:[["router","analyst"],["router","planner"],["router","coder"],["analyst","planner"],
    ["analyst","coder"],["planner","coder"],["coder","tester"],["tester","reviewer"],["tester","finish"],["reviewer","finish"]]};

test("selects level during setup, preserves history across pages and supports legacy traces",()=>{
  assert.equal(topologyForRun([]),null);
  for(const [level,name] of [["L1","linear"],["L2","linear-routed"],["L3","chain"]]) {
    assert.equal(topologyForRun([{event:"benchmark_phase_started",level}]),name);
    assert.equal(topologyForRun([],level),name);
  }
  assert.equal(topologyForRun([{event:"run_started",topology:"linear-routed"},{event:"llm_call"}]),"linear-routed");
  assert.equal(topologyForRun([{event:"agent_started"}]),"chain");
  assert.equal(topologyForRun([],"L4"),null);
});

const routing = {selected_roles:["Coder","Tester"],skipped:[{role:"Analyst",reason:"Already specified"},
  {role:"Planner",reason:"Localized fix"},{role:"Reviewer",reason:"Direct test"}]};
const events = [{event:"agent_started",agent:"router"},{event:"agent_completed",agent:"router"},
  {event:"routing_decided",...routing},{event:"agent_started",agent:"coder"}];

test("Router, skipped reasons and only selected branch edges are displayed",()=>{
  const result=summarize(topology,events);
  assert.equal(result.nodes.get("router").status,"done");
  assert.equal(result.nodes.get("coder").status,"running");
  assert.equal(result.nodes.get("planner").status,"skipped");
  assert.equal(result.nodes.get("planner").skipReason,"Localized fix");
  assert.equal(result.edges.find(e=>e.from==="router"&&e.to==="coder").active,true);
  assert.equal(result.edges.find(e=>e.to==="analyst").skipped,true);
  const fallback=summarize(topology,[...events.filter(e=>e.event!=="routing_decided"),
    {event:"routing_decided",selected_roles:roles.slice(1),skipped:[]}]);
  assert.equal(fallback.nodes.get("planner").status,"idle");
});

test("completed historical L2 metadata provides skipped roles and a finish node",()=>{
  const result=summarize(topology,[{event:"run_completed",summary:"PASS"}],routing);
  assert.equal(result.nodes.get("reviewer").status,"skipped");
  assert.equal(result.nodes.get("finish").status,"done");
  assert.equal(result.nodes.get("finish").handoff,"PASS");
  const oldTrace=summarize(topology,[{event:"agent_completed",agent:"router",...routing}]);
  assert.equal(oldTrace.nodes.get("planner").status,"skipped");
});

const source=readFileSync(new URL("../../../src/raac/common/trace_assets/viewer.js",import.meta.url),"utf8");

test("late topology responses cannot overwrite a newer level and failed fetch can retry",async()=>{
  const pending=[]; const elements=new Map();
  const context=vm.createContext({flow:{name:"",request:0},
    $:id=>{if(!elements.has(id))elements.set(id,{replaceChildren(){}});return elements.get(id);},
    node:()=>({}),renderFlow:()=>{},traceFetch:()=>new Promise(resolve=>pending.push(resolve))});
  vm.runInContext(source.slice(source.indexOf("async function loadTopology("),source.indexOf("function resetMemoryChanges")),context);
  const first=context.loadTopology("linear"),second=context.loadTopology("linear-routed");
  pending[1]({ok:true,json:async()=>topology});await second;
  pending[0]({ok:true,json:async()=>({topology_id:"linear"})});await first;
  assert.equal(context.flow.topology.topology_id,"linear-routed");
  const failed=context.loadTopology("unknown");pending[2]({ok:false,status:404});await failed;
  assert.equal(context.flow.topology,null);
  assert.equal(context.flow.name,"");
});

test("SVG render includes Router, skipped accessible labels and finish without runtime errors",()=>{
  const svgNodes=[];
  const element=()=>({append(){},replaceChildren(){},addEventListener(){},style:{setProperty(){}}});
  const context=vm.createContext({layout,summarize,STATUS_LABELS,
    flow:{topology,selected:null},state:{events,routing},names:{router:"Router"},colors:{mas:"gray"},
    $:()=>element(),svg:(tag,attrs,text)=>{svgNodes.push({tag,attrs,text});return element();}});
  vm.runInContext(source.slice(source.indexOf("function clip("),source.indexOf("async function loadTopology("))+
    "\nrenderFlowDetail=()=>{};renderFlow();",context);
  assert.ok(svgNodes.some(x=>x.attrs?.["aria-label"]?.startsWith("Router:")));
  assert.ok(svgNodes.some(x=>x.attrs?.["aria-label"]?.includes("Bỏ qua")));
  assert.ok(svgNodes.some(x=>x.text==="Kết quả"));
});

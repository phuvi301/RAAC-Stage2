import assert from "node:assert/strict";
import test from "node:test";
import {indexUsage, tokenLabel, cacheTokens, summarizeCache} from "../../../src/raac/common/trace_assets/usage.mjs";

const call=(id,role,input,output)=>({id,event:"llm_call",llm_call_id:id,agent_call_id:role,
  usage:{prompt_tokens:input,completion_tokens:output}});

test("request, response and tool copies do not duplicate API token charges",()=>{
  const events=[{id:"req",event:"llm_request",llm_call_id:"c1"},call("c1","a1",7095,87),
    {id:"response",event:"llm_response",llm_call_id:"c1",usage:{prompt_tokens:7095,completion_tokens:87}},
    {id:"tool",event:"tool_call_completed",llm_call_id:"c1"}];
  const totals=indexUsage(events).byCall.get("c1");
  assert.equal(totals.input,7095);assert.equal(totals.output,87);assert.equal(totals.attempts,1);
});

test("retry with missing usage is a lower bound, never a fake complete zero",()=>{
  const missing={...call("c1","a1",0,0),id:"failed-attempt",usage:null};
  const indexed=indexUsage([missing,call("c1","a1",800,20)]);
  assert.equal(tokenLabel(indexed.byCall.get("c1"),"input"),"≥ 800");
  assert.equal(tokenLabel(indexed.byCall.get("c1"),"output"),"≥ 20");
  assert.equal(indexed.byCall.get("c1").attempts,2);
  assert.equal(indexed.byEvent.get("c1").input,800);
});

test("token data joins when later pages bring the response usage",()=>{
  const request={id:"req",event:"llm_request",llm_call_id:"c1"};
  assert.equal(tokenLabel(indexUsage([request]).byCall.get("c1"),"input"),"—");
  assert.equal(tokenLabel(indexUsage([request,call("c1","a1",100,9)]).byCall.get("c1"),"input"),"100");
});

test("agent totals stay isolated between separate invocations of the same role",()=>{
  const indexed=indexUsage([call("c1","coder-round-1",10,2),call("c2","coder-round-1",20,3),call("c3","coder-round-2",40,8)]);
  assert.equal(indexed.byAgent.get("coder-round-1").input,30);
  assert.equal(indexed.byAgent.get("coder-round-1").output,5);
  assert.equal(indexed.byAgent.get("coder-round-2").input,40);
});

test("observed zero and missing or invalid token fields are distinguished",()=>{
  const indexed=indexUsage([call("zero","a1",0,0),call("bad","a2",-3,undefined)]);
  assert.equal(tokenLabel(indexed.byCall.get("zero"),"input"),"0");
  assert.equal(tokenLabel(indexed.byCall.get("bad"),"input"),"—");
  assert.equal(tokenLabel(indexed.byCall.get("bad"),"output"),"—");
});

test("missing correlation IDs are not merged and input events remain unchanged",()=>{
  const events=[{id:"one",event:"llm_call",usage:{prompt_tokens:12,completion_tokens:4}},call("two","a2",30,5)];
  const original=JSON.stringify(events), indexed=indexUsage(events);
  assert.equal(indexed.byCall.size,1);assert.equal(indexed.byAgent.size,1);
  assert.equal(indexed.byEvent.get("one").input,12);assert.equal(JSON.stringify(events),original);
});


test("cache is an input subset and joins role/call without duplicated tool charges",()=>{
  const first=call("c1","a1",100,10);first.usage.cached_tokens=60;
  const second=call("c2","a1",120,20);second.usage.cached_tokens=0;
  const events=[first,second,{id:"tool",event:"tool_call_completed",llm_call_id:"c1",usage:first.usage}];
  const indexed=indexUsage(events);
  assert.equal(indexed.byAgent.get("a1").input,220);
  assert.equal(tokenLabel(indexed.byAgent.get("a1"),"cached"),"60");
  assert.equal(tokenLabel(indexed.byCall.get("c2"),"cached"),"0");
  assert.equal(summarizeCache(events),60);
});

test("missing and invalid cache including old logs never become zero",()=>{
  const known=call("c1","a1",100,20);known.usage.cached_tokens=60;
  const missing=call("c2","a1",100,20);
  assert.equal(tokenLabel(indexUsage([known,missing]).byAgent.get("a1"),"cached"),"—");
  assert.equal(summarizeCache([known,missing]),null);
  for(const value of [-1,101,true,1.5,"60",null]) {
    assert.equal(cacheTokens({prompt_tokens:100,cached_tokens:value}),null);
  }
  assert.equal(cacheTokens({prompt_tokens:100,prompt_tokens_details:{cached_tokens:60}}),60);
});

test("final cache snapshot wins over a partial feed, old final totals stay unknown",()=>{
  const final={event:"run_completed",usage:{prompt_tokens:1000,cached_tokens:800,cache_usage_complete:true}};
  assert.equal(summarizeCache([call("c1","a1",10,2),final]),800);
  assert.equal(summarizeCache([{...final,usage:{prompt_tokens:1000}}]),null);
  assert.equal(summarizeCache([{...final,usage:{...final.usage,cache_usage_complete:false}}]),null);
});

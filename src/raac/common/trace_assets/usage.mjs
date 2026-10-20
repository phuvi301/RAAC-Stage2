// Join API usage by real call IDs. Tool events never add token charges.
function emptyUsage() {
  return {input:null, output:null, cached:null, inputComplete:true, outputComplete:true, cachedComplete:true, attempts:0};
}
function addUsage(total, usage) {
  total.attempts++;
  for (const [field, source] of [["input","prompt_tokens"],["output","completion_tokens"]]) {
    const value=usage?.[source];
    if(Number.isInteger(value) && value>=0)total[field]=(total[field]??0)+value;
    else total[field+"Complete"]=false;
  }
  const cached=cacheTokens(usage);
  if(cached===null)total.cachedComplete=false;
  else total.cached=(total.cached??0)+cached;
}
export function cacheTokens(usage) {
  const details=usage?.prompt_tokens_details;
  const value=details && Object.hasOwn(details,"cached_tokens")?details.cached_tokens:usage?.cached_tokens;
  return Number.isInteger(value) && value>=0 && Number.isInteger(usage?.prompt_tokens) && value<=usage.prompt_tokens?value:null;
}
export function summarizeCache(events) {
  let cached=0,calls=0,complete=true,final=null;
  for(const event of events) {
    if(event.event==="run_completed")final=event.usage;
    if(event.event!=="llm_call")continue;
    calls++;
    const value=cacheTokens(event.usage);
    if(value===null)complete=false;else cached+=value;
  }
  if(final)return final.cache_usage_complete===false?null:cacheTokens(final);
  return calls && complete?cached:null;
}
export function indexUsage(events) {
  const byCall=new Map(), byAgent=new Map(), byEvent=new Map();
  for(const event of events) {
    if(event.event!=="llm_call")continue;
    const own=emptyUsage();addUsage(own,event.usage);byEvent.set(event.id,own);
    for(const [index,key] of [[byCall,event.llm_call_id],[byAgent,event.agent_call_id]]) {
      if(!key)continue;
      if(!index.has(key))index.set(key,emptyUsage());
      addUsage(index.get(key),event.usage);
    }
  }
  return {byCall,byAgent,byEvent};
}
export function tokenLabel(usage, field) {
  if(field==="cached" && !usage?.cachedComplete)return "—";
  if(usage?.[field]===null || usage?.[field]===undefined)return "—";
  return (usage[field+"Complete"]?"":"≥ ")+usage[field].toLocaleString("en-US");
}

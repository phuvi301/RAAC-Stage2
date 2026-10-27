import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../../src/raac/common/trace_assets/viewer.js", import.meta.url), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf("function mergeResponseError("), source.indexOf("async function poll()")) +
  "\nglobalThis.merge = mergeResponseError;", context);

test("paired errors merge response detail into one call, including across poll pages", () => {
  const call = {id:"1", event:"llm_call", run_id:"run", llm_call_id:"call", attempt:1, llm_error:true, payloads:[]};
  const response = {...call, id:"2", event:"llm_response", payloads:[{label:"response",data:"partial JSON"}]};
  assert.equal(context.merge([call], response), call);
  assert.equal(call.payloads[0].data, "partial JSON");
  assert.equal(context.merge([], response), null);
  assert.equal(context.merge([call], {...response, run_id:"other"}), null);
  assert.equal(context.merge([call], {...response, attempt:2}), null);
  assert.equal(context.merge([call], {...response, llm_error:false}), null);
});

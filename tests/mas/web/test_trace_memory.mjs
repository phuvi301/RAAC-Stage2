import assert from "node:assert/strict";
import test from "node:test";
import {memorySummary,memoryEntryTitle,mergeMemoryPage,memoryRevisionLabel,memoryChangeSummary,mergeMemoryHistory} from "../../../src/raac/common/trace_assets/memory.mjs";

test("memory status distinguishes revisions, stored token estimate and compactions",()=>{
  const label=memorySummary({revision:12,estimated_tokens:3900,compactions:2});
  assert.match(label,/12/);assert.match(label,/3900/);assert.match(label,/2 lần/);
  assert.match(memoryEntryTitle({sequence:3,round:2,kind:"tool",tool:"read_file"}),/vòng 2.*read_file/);
});
test("journal pagination deduplicates repeated pages and preserves source objects",()=>{
  const first={id:"one",entries:[{sequence:1},{sequence:2}],cursor:40};
  const next={id:"one",entries:[{sequence:2},{sequence:3}],cursor:70,has_more:false};
  const original=JSON.stringify([first,next]);
  assert.deepEqual(mergeMemoryPage(first,next).entries.map(e=>e.sequence),[1,2,3]);
  assert.equal(JSON.stringify([first,next]),original);
});
test("changing scope or truncating a journal clears the previous entries",()=>{
  const first={id:"one",entries:[{sequence:99}],cursor:999};
  for(const page of [{id:"two",entries:[{sequence:1}],cursor:10},{id:"one",reset:true,entries:[{sequence:1}],cursor:10}])
    assert.deepEqual(mergeMemoryPage(first,page).entries,[{sequence:1}]);
});

test("history merges revisions in newest-first order without leaking another scope",()=>{
  const first={id:"one",revisions:[{revision:3},{revision:2}],cursor:2};
  const next={id:"one",revisions:[{revision:2},{revision:1}],cursor:4};
  const source=JSON.stringify([first,next]);
  assert.deepEqual(mergeMemoryHistory(first,next).revisions.map(r=>r.revision),[3,2,1]);
  assert.equal(JSON.stringify([first,next]),source);
  assert.deepEqual(mergeMemoryHistory(first,{...next,id:"two"}).revisions.map(r=>r.revision),[2,1]);
});
test("revision and change labels distinguish compact, append and unavailable history",()=>{
  assert.match(memoryRevisionLabel({revision:4,round:2,tool:"read_file",compacted:true}),/4.*vòng 2.*read_file.*COMPACT/);
  const label=memoryChangeSummary({available:true,previous_revision:3,revision:4,added:[{}],removed:[{},{}],updated:[{}],metadata:{compacted:true}});
  assert.match(label,/3 → 4/);assert.match(label,/1 thêm/);assert.match(label,/2 bỏ/);assert.match(label,/Đã compact/);
  assert.match(memoryChangeSummary({available:false}),/Không đủ dữ liệu/);
});

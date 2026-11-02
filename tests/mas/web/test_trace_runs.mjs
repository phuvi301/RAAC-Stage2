import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../../src/raac/common/trace_assets/viewer.js", import.meta.url), "utf8");
const functions = source.slice(source.indexOf("const $ ="), source.indexOf("const colors ="));

function fixture(search = "") {
  const select = {options: [], value: "", replaceChildren(...items) {this.options = items;}, add(item) {this.options.push(item);}};
  const picker = {hidden: true};
  const calls = [];
  const context = vm.createContext({URL, URLSearchParams,
    Option: class {constructor(text, value) {this.text = text; this.value = value;}},
    document: {getElementById(id) {return id === "run-select" ? select : picker;}},
    location: {search, origin: "http://127.0.0.1:12345", reload() {context.reloaded = true;}},
    listing: {runs: ["first"], active: "first"},
    fetch: async (url) => {calls.push(String(url)); return {ok: true, json: async () => context.listing};},
  });
  vm.runInContext(functions + "\nglobalThis.driver = {refreshRuns, traceFetch};", context);
  return {context, select, picker, calls, driver: context.driver};
}

test("auto follow registers options and reloads when the active run changes", async () => {
  const f = fixture();
  assert.equal(await f.driver.refreshRuns(), true);
  assert.equal(f.picker.hidden, false);
  assert.equal(f.select.options.length, 2);
  f.context.listing = {runs: ["first", "second"], active: "second"};
  assert.equal(await f.driver.refreshRuns(), false);
  assert.equal(f.context.reloaded, true);
});

test("pinned history keeps event and memory requests on the same run", async () => {
  const f = fixture("?run=first");
  await f.driver.refreshRuns();
  f.context.listing = {runs: ["first", "second"], active: "second"};
  assert.equal(await f.driver.refreshRuns(), true);
  assert.equal(f.context.reloaded, undefined);
  assert.equal(f.select.value, "first");
  await f.driver.traceFetch("/api/events?cursor=10");
  await f.driver.traceFetch("/api/memory?id=notes");
  assert.match(f.calls.at(-2), /cursor=10&run=first/);
  assert.match(f.calls.at(-1), /id=notes&run=first/);
});

test("standalone show-log keeps its original requests and hides the picker", async () => {
  const f = fixture();
  f.context.listing = {runs: [], active: null};
  assert.equal(await f.driver.refreshRuns(), true);
  await f.driver.traceFetch("/api/events?cursor=0");
  assert.equal(f.picker.hidden, true);
  assert.equal(new URL(f.calls.at(-1)).searchParams.has("run"), false);
});

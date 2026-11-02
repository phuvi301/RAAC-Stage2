import assert from "node:assert/strict";
import {readFileSync, readdirSync} from "node:fs";
import {spawnSync} from "node:child_process";
import test from "node:test";

const assets = new URL("../../../src/raac/common/trace_assets/", import.meta.url);

test("all viewer modules parse in full before the browser starts polling", () => {
  for (const name of readdirSync(assets).filter(name => /\.(?:js|mjs)$/.test(name))) {
    const source = readFileSync(new URL(name, assets), "utf8");
    const checked = spawnSync(process.execPath, ["--input-type=module", "--check"], {
      input: source, encoding: "utf8", windowsHide: true,
    });
    assert.equal(checked.status, 0, `${name}: ${checked.error || checked.stderr}`);
  }
});

test("served HTML, CSS and scripts contain no unresolved merge blocks", () => {
  for (const name of readdirSync(assets).filter(name => /\.(?:html|css|js|mjs)$/.test(name))) {
    const source = readFileSync(new URL(name, assets), "utf8");
    assert.doesNotMatch(source, /^(?:<{7}(?: |$)|={7}\s*$|>{7}(?: |$))/m, name);
  }
});

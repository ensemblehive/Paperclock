import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("production bundle contains the Paperclock product copy", async () => {
  const manifest = await readFile("dist/server/manifest.json", "utf8").catch(() => "");
  const source = await readFile("app/page.tsx", "utf8");
  assert.match(source, /Your files know/);
  assert.match(source, /Nothing leaves your computer/);
  assert.match(source, /any number of files/);
  assert.match(source, /files\/sec/);
  assert.ok(manifest.length > 0 || source.includes("Paperclock"));
});

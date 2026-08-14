import assert from "node:assert/strict";
import { once } from "node:events";
import { pathToFileURL } from "node:url";
import { Worker } from "node:worker_threads";
import test from "node:test";

const workerUrl = pathToFileURL(`${process.cwd()}/public/file-prep-worker.js`).href;

test("background worker prepares binary files without touching the main thread", async () => {
  const wrapper = `
    const { parentPort } = require("node:worker_threads");
    global.self = globalThis;
    self.postMessage = (message) => parentPort.postMessage(message);
    import(${JSON.stringify(workerUrl)}).then(() => {
      parentPort.on("message", (data) => self.onmessage({ data }));
      parentPort.postMessage({ type: "ready" });
    });
  `;
  const worker = new Worker(wrapper, { eval: true });
  await once(worker, "message");

  const bytes = new Uint8Array(7_900_003);
  for (let index = 0; index < bytes.length; index += 1) bytes[index] = index % 251;
  const file = new File([bytes], "policy.pdf", { type: "application/pdf" });
  worker.postMessage({
    entries: [{
      path: "Policies/policy.pdf",
      size: file.size,
      modified: "2026-08-14T00:00:00.000Z",
      fingerprint: "policy-fingerprint",
      file,
    }],
    dateOrder: "day-first",
  });

  const messages = [];
  while (true) {
    const [message] = await once(worker, "message");
    messages.push(message);
    if (message.type === "done") break;
    if (message.type === "error") throw new Error(message.message);
  }
  await worker.terminate();

  const done = messages.at(-1);
  const payload = JSON.parse(await done.body.text());
  assert.equal(payload.files[0].encoding, "base64");
  assert.equal(payload.files[0].content, Buffer.from(bytes).toString("base64"));
  assert.ok(messages.some((message) => message.type === "progress"));
});

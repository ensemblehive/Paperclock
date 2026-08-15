const MAX_FILE_BYTES = 8 * 1024 * 1024;
const BASE64_CHUNK = 32_766;
let lastProgressAt = 0;

self.onmessage = async (event) => {
  const { entries, dateOrder } = event.data;
  try {
    const files = [];
    for (let index = 0; index < entries.length; index += 1) {
      const entry = entries[index];
      report(entry, index, entries.length, 0, entry.size, true);
      if (entry.prepared) {
        files.push({ ...entry.prepared, fingerprint: entry.fingerprint });
        report(entry, index, entries.length, entry.size, entry.size);
        continue;
      }
      if (entry.size > MAX_FILE_BYTES) {
        files.push({
          path: entry.path,
          content: "",
          encoding: "text",
          modified: entry.modified,
          fingerprint: entry.fingerprint,
          skip_reason: `${entry.path}: larger than the 8 MB per-file safety limit`,
        });
        report(entry, index, entries.length, entry.size, entry.size);
        continue;
      }

      const extension = entry.path.split(".").pop()?.toLowerCase() ?? "";
      const binary = extension === "pdf" || extension === "docx" || extension === "pages" || extension === "msg";
      const content = binary
        ? await readBase64(entry, index, entries.length)
        : await readText(entry, index, entries.length);
      files.push({
        path: entry.path,
        content,
        encoding: binary ? "base64" : "text",
        modified: entry.modified,
        fingerprint: entry.fingerprint,
      });
    }
    const body = new Blob([JSON.stringify({ files, date_order: dateOrder })], {
      type: "application/json",
    });
    self.postMessage({ type: "done", body });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "File preparation failed" });
  }
};

async function readText(entry, index, count) {
  const reader = entry.file.stream().getReader();
  const decoder = new TextDecoder();
  const parts = [];
  let loaded = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    loaded += value.byteLength;
    parts.push(decoder.decode(value, { stream: true }));
    report(entry, index, count, loaded, entry.size);
  }
  parts.push(decoder.decode());
  report(entry, index, count, entry.size, entry.size);
  return parts.join("");
}

async function readBase64(entry, index, count) {
  const reader = entry.file.stream().getReader();
  const parts = [];
  let carry = new Uint8Array(0);
  let loaded = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    loaded += value.byteLength;
    const combined = new Uint8Array(carry.length + value.length);
    combined.set(carry);
    combined.set(value, carry.length);
    const usable = combined.length - (combined.length % 3);
    if (usable) parts.push(bytesToBase64(combined.subarray(0, usable)));
    carry = combined.slice(usable);
    report(entry, index, count, loaded, entry.size);
  }
  if (carry.length) parts.push(bytesToBase64(carry));
  report(entry, index, count, entry.size, entry.size);
  return parts.join("");
}

function bytesToBase64(bytes) {
  const encoded = [];
  for (let offset = 0; offset < bytes.length; offset += BASE64_CHUNK) {
    encoded.push(btoa(String.fromCharCode(...bytes.subarray(offset, offset + BASE64_CHUNK))));
  }
  return encoded.join("");
}

function report(entry, index, count, loaded, total, force = false) {
  const now = performance.now();
  if (!force && loaded < total && now - lastProgressAt < 80) return;
  lastProgressAt = now;
  self.postMessage({ type: "progress", path: entry.path, index, count, loaded, total });
}

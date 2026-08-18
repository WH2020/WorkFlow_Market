import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { openWebSource, requestPinnedSourceForTests, setSourceRequestForTests, validatePublicUrl } from "../extensions/source-readers.ts";

test("pinned source transport connects to the vetted address without a second DNS lookup", async () => {
  const server = createServer((request, response) => {
    assert.match(request.headers.host ?? "", /^dns-rebind\.invalid:/u);
    response.writeHead(200, { "Content-Type": "text/plain" });
    response.end("pinned transport reached vetted address");
  });
  await new Promise<void>((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  try {
    const address = server.address();
    assert(address && typeof address === "object");
    const response = await requestPinnedSourceForTests(
      new URL(`http://dns-rebind.invalid:${address.port}/evidence`),
      { address: "127.0.0.1", family: 4 },
    );
    assert.equal(response.status, 200);
    assert.equal(await response.text(), "pinned transport reached vetted address");
  } finally {
    await new Promise<void>((resolvePromise, reject) => server.close((error) => error ? reject(error) : resolvePromise()));
  }
});

test("public URL validation rejects private and reserved network targets", async () => {
  await assert.rejects(() => validatePublicUrl("http://127.0.0.1/admin"), /回环|私网/);
  await assert.rejects(() => validatePublicUrl("https://192.0.2.10/report"), /保留地址|私网|回环/);
  await assert.rejects(
    () => validatePublicUrl("https://example.com/report?X-Amz-Signature=signed-secret"),
    /密钥|令牌/u,
  );
  await assert.rejects(
    () => validatePublicUrl("https://example.com/report#credential=local-secret"),
    /密钥|令牌/u,
  );
});

test("online PDF parse failure fails closed instead of running fallback in the agent process", async () => {
  const stream = "BT /F1 12 Tf 72 720 Td (fallback text must not be accepted online) Tj ET";
  const pdf = [
    "%PDF-1.4",
    "1 0 obj", "<< /Type /Page /Contents 2 0 R >>", "endobj",
    "2 0 obj", `<< /Length ${stream.length} >>`, "stream", stream, "endstream", "endobj",
    "%%EOF",
  ].join("\n");
  setSourceRequestForTests(async () => new Response(Buffer.from(pdf, "latin1"), {
    status: 200,
    headers: { "Content-Type": "application/pdf" },
  }));
  try {
    await assert.rejects(
      () => openWebSource("https://93.184.216.34/fallback.pdf", { maxPages: 5, maxChars: 5000 }),
      /在线 PDF 解析失败，已安全停止/u,
    );
  } finally {
    setSourceRequestForTests(undefined);
  }
});

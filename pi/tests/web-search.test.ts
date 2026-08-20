import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";

import { searchPublicWeb } from "../extensions/web-search.ts";

test("scene-aware public search deduplicates URLs and rejects unsafe constraints", async () => {
  const originalKey = process.env.BRAVE_SEARCH_API_KEY;
  const originalProvider = process.env.DIRECTOR_SEARCH_PROVIDER;
  const originalFetch = globalThis.fetch;
  let calls = 0;
  try {
    delete process.env.BRAVE_SEARCH_API_KEY;
    delete process.env.DIRECTOR_SEARCH_PROVIDER;
    globalThis.fetch = (async (_input, init) => {
      calls += 1;
      const request = JSON.parse(String(init?.body)) as { query: string };
      return new Response(JSON.stringify({ results: [
        { title: `政府来源 ${request.query}`, url: "https://www.gov.cn/shared", snippet: "正文候选" },
        { title: `普通来源 ${request.query}`, url: `https://example.com/${calls}`, snippet: "普通候选" },
      ] }), { status: 200, headers: { "Content-Type": "application/json" } });
    }) as typeof fetch;
    const response = await searchPublicWeb({
      queries: ["具身智能政策", "机器人政策"], mode: "official", count: 5,
    });
    assert.equal(response.provider, "keenable-public");
    assert.equal(response.evidence_status, "discovery_only");
    assert.equal(response.searches[0]?.results[0]?.source_category_hint, "government");
    assert.equal(response.searches[0]?.results.length, 2);
    assert.equal(response.searches[1]?.results.length, 1);
    assert.equal(calls, 2);

    const inferred = await searchPublicWeb({ queries: ["上海市具身智能产业政策"], count: 3 });
    assert.equal(inferred.requested_mode, "auto");
    assert.equal(inferred.mode, "chinese_policy");
    assert.equal(inferred.applied_filters.site, "gov.cn");
    assert.equal(calls, 3);

    await assert.rejects(
      () => searchPublicWeb({ queries: ["政策"], site: "127.0.0.1" }),
      /公网域名/,
    );
    await assert.rejects(
      () => searchPublicWeb({ queries: ["政策"], published_after: "2026-02-31" }),
      /有效的 YYYY-MM-DD/,
    );
  } finally {
    if (originalKey === undefined) delete process.env.BRAVE_SEARCH_API_KEY;
    else process.env.BRAVE_SEARCH_API_KEY = originalKey;
    if (originalProvider === undefined) delete process.env.DIRECTOR_SEARCH_PROVIDER;
    else process.env.DIRECTOR_SEARCH_PROVIDER = originalProvider;
    globalThis.fetch = originalFetch;
  }
});

test("public search stops reading a streamed response beyond the byte budget", async () => {
  const originalKey = process.env.BRAVE_SEARCH_API_KEY;
  const originalProvider = process.env.DIRECTOR_SEARCH_PROVIDER;
  const originalFetch = globalThis.fetch;
  try {
    delete process.env.BRAVE_SEARCH_API_KEY;
    process.env.DIRECTOR_SEARCH_PROVIDER = "keenable-public";
    globalThis.fetch = (async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(1024 * 1024));
        controller.enqueue(new Uint8Array(1024 * 1024 + 1));
        controller.close();
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })) as typeof fetch;
    await assert.rejects(
      () => searchPublicWeb({ queries: ["公开政策"] }),
      /超过 2 MiB/,
    );
  } finally {
    if (originalKey === undefined) delete process.env.BRAVE_SEARCH_API_KEY;
    else process.env.BRAVE_SEARCH_API_KEY = originalKey;
    if (originalProvider === undefined) delete process.env.DIRECTOR_SEARCH_PROVIDER;
    else process.env.DIRECTOR_SEARCH_PROVIDER = originalProvider;
    globalThis.fetch = originalFetch;
  }
});

test("configured One Search gateway takes priority and keeps upstream provenance", async () => {
  const names = [
    "ONE_SEARCH_BASE_URL", "ONE_SEARCH_API_TOKEN", "ONE_SEARCH_MODE",
    "ONE_SEARCH_MAX_RESULTS", "ONE_SEARCH_ALLOW_PRIVATE_NETWORK", "ONE_SEARCH_PROVIDERS",
    "DIRECTOR_SEARCH_PROVIDER",
  ] as const;
  const original = Object.fromEntries(names.map((name) => [name, process.env[name]]));
  let received: Record<string, unknown> = {};
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    request.on("end", () => {
      received = JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
      assert.equal(request.url, "/v1/search");
      assert.equal(request.headers.authorization, "Bearer osr_runtime_test");
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ results: [
        { title: "官方政策", url: "https://www.gov.cn/policy", content: "政策正文候选", provider: "brave" },
      ] }));
    });
  });
  await new Promise<void>((resolvePromise) => server.listen(0, "127.0.0.1", resolvePromise));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test server failed to bind");
  try {
    process.env.ONE_SEARCH_BASE_URL = `http://127.0.0.1:${address.port}`;
    process.env.ONE_SEARCH_API_TOKEN = "osr_runtime_test";
    process.env.ONE_SEARCH_MODE = "parallel";
    process.env.ONE_SEARCH_MAX_RESULTS = "3";
    process.env.ONE_SEARCH_ALLOW_PRIVATE_NETWORK = "1";
    process.env.ONE_SEARCH_PROVIDERS = JSON.stringify(["brave", "tavily"]);
    delete process.env.DIRECTOR_SEARCH_PROVIDER;
    const response = await searchPublicWeb({ queries: ["具身智能政策"], mode: "chinese_policy", count: 8 });
    assert.equal(response.provider, "one-search");
    assert.equal(response.searches[0]?.provider, "one-search");
    assert.equal(response.searches[0]?.results[0]?.upstream_provider, "brave");
    assert.equal(response.applied_filters.count, 3);
    assert.equal(received.mode, "parallel");
    assert.equal(received.limit, 3);
    assert.deepEqual(received.providers, ["brave", "tavily"]);
    assert.match(String(received.query), /site:gov\.cn/u);
  } finally {
    await new Promise<void>((resolvePromise, reject) => server.close((error) => error ? reject(error) : resolvePromise()));
    names.forEach((name) => {
      const value = original[name];
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    });
  }
});

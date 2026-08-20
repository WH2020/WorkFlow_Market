import assert from "node:assert/strict";
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

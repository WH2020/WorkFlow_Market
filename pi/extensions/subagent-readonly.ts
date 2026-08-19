import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { normalizePublicUrl, openWebSource } from "./source-readers.ts";
import {
  loadGovernedSubagentContract,
  recordGovernedSearchUrls,
  recordGovernedSources,
  type GovernedSubagentSource,
} from "./subagent-contracts.ts";

const MAX_WEB_RESPONSE_BYTES = 2 * 1024 * 1024;
const SAFE_CONTRACT_ID = "^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$";

function resultContent(value: unknown) {
  const text = JSON.stringify(value, null, 2);
  if (Buffer.byteLength(text, "utf8") > MAX_WEB_RESPONSE_BYTES) throw new Error("Subagent tool result exceeds 2 MiB");
  return [{ type: "text" as const, text }];
}

export function assertSafePublicQueryForTests(value: string): string {
  const query = value.trim();
  if (!query || query.length > 400 || query.split(/\s+/u).length > 50) {
    throw new Error("公开检索词必须为 1-400 字符且不超过 50 个词");
  }
  const forbidden = [
    /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/iu,
    /(?<!\d)1[3-9]\d{9}(?!\d)/u,
    /(?<!\d)\d{17}[\dXx](?!\d)/u,
    /\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b/u,
    /(?:api[_ -]?key|access[_ -]?token|password|passwd|secret)\s*[:=]\s*\S+/iu,
  ];
  if (forbidden.some((pattern) => pattern.test(query))) {
    throw new Error("公开检索词疑似包含账号、密钥或个人敏感信息，已拒绝发送");
  }
  return query;
}

function requireContract(projectRoot: string, contractId: string, logicalTool: "web.search" | "web.open") {
  const contract = loadGovernedSubagentContract(projectRoot, contractId);
  if (contract.role !== "research-scout" || !contract.allowed_tools.includes(logicalTool)) {
    throw new Error(`受管 Subagent 合同未授权 ${logicalTool}`);
  }
  return contract;
}

export default function governedReadonlySubagent(pi: ExtensionAPI): void {
  const projectRoot = process.cwd();

  pi.registerTool({
    name: "director_child_web_search",
    label: "Subagent 公开检索",
    description: "仅供受管只读研究 Subagent 使用。按 contract_id 检索公开来源并登记可读取 URL。",
    parameters: Type.Object({
      contract_id: Type.String({ pattern: SAFE_CONTRACT_ID }),
      queries: Type.Array(Type.String({ minLength: 1, maxLength: 400 }), { minItems: 1, maxItems: 6, uniqueItems: true }),
      count: Type.Optional(Type.Number({ minimum: 1, maximum: 8 })),
      country: Type.Optional(Type.String({ pattern: "^[A-Za-z]{2}$" })),
      search_lang: Type.Optional(Type.String({ pattern: "^[A-Za-z-]{2,10}$" })),
    }),
    async execute(_toolCallId, params) {
      requireContract(projectRoot, params.contract_id, "web.search");
      const key = process.env.BRAVE_SEARCH_API_KEY?.trim();
      if (!key) throw new Error("公开检索尚未配置 BRAVE_SEARCH_API_KEY");
      const queries = params.queries.map(assertSafePublicQueryForTests);
      if (new Set(queries).size !== queries.length) throw new Error("同一批公开检索不能包含重复词");
      const searches = [];
      const registeredUrls: string[] = [];
      const totalSignal = AbortSignal.timeout(60_000);
      for (const query of queries) {
        const endpoint = new URL("https://api.search.brave.com/res/v1/web/search");
        endpoint.searchParams.set("q", query);
        endpoint.searchParams.set("count", String(Math.trunc(params.count ?? 8)));
        if (params.country) endpoint.searchParams.set("country", params.country.toUpperCase());
        if (params.search_lang) endpoint.searchParams.set("search_lang", params.search_lang.toLowerCase());
        const response = await fetch(endpoint, {
          headers: { Accept: "application/json", "X-Subscription-Token": key },
          signal: totalSignal,
          redirect: "error",
        });
        if (!response.ok) throw new Error(`公开检索失败（HTTP ${response.status}）`);
        const declared = Number(response.headers.get("Content-Length") ?? "0");
        if (Number.isFinite(declared) && declared > MAX_WEB_RESPONSE_BYTES) throw new Error("公开检索响应超过 2 MiB");
        const body = await response.text();
        if (Buffer.byteLength(body, "utf8") > MAX_WEB_RESPONSE_BYTES) throw new Error("公开检索响应超过 2 MiB");
        const payload = JSON.parse(body) as {
          web?: { results?: Array<{ title?: unknown; url?: unknown; description?: unknown; age?: unknown }> };
        };
        const results = (payload.web?.results ?? []).slice(0, 8).flatMap((item) => {
          if (typeof item.title !== "string" || typeof item.url !== "string") return [];
          try {
            const url = normalizePublicUrl(item.url).toString().slice(0, 2048);
            registeredUrls.push(url);
            return [{
              title: item.title.slice(0, 500),
              url,
              description: typeof item.description === "string" ? item.description.slice(0, 2000) : "",
              age: typeof item.age === "string" ? item.age.slice(0, 100) : "",
            }];
          } catch {
            return [];
          }
        });
        searches.push({ query, results });
      }
      recordGovernedSearchUrls(projectRoot, params.contract_id, registeredUrls);
      const result = { provider: "brave", searched_at: new Date().toISOString(), searches };
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "director_child_web_open",
    label: "Subagent 来源正文读取",
    description: "仅读取同一受管合同的检索结果或用户明确提供的 URL，并登记来源哈希。",
    parameters: Type.Object({
      contract_id: Type.String({ pattern: SAFE_CONTRACT_ID }),
      items: Type.Array(Type.Object({
        url: Type.String({ minLength: 1, maxLength: 2048 }),
        title: Type.Optional(Type.String({ maxLength: 500 })),
      }), { minItems: 1, maxItems: 4 }),
      max_pages: Type.Optional(Type.Number({ minimum: 1, maximum: 100 })),
      max_chars: Type.Optional(Type.Number({ minimum: 1000, maximum: 100000 })),
    }),
    async execute(_toolCallId, params) {
      const contract = requireContract(projectRoot, params.contract_id, "web.open");
      const allowed = new Set([...contract.searched_urls, ...contract.authorized_urls]);
      const normalizedItems = params.items.map((item) => ({ ...item, url: normalizePublicUrl(item.url).toString() }));
      if (new Set(normalizedItems.map((item) => item.url)).size !== normalizedItems.length) {
        throw new Error("同一批来源正文读取不能包含重复 URL");
      }
      for (const item of normalizedItems) {
        if (!allowed.has(item.url)) throw new Error("该 URL 不属于本次受管检索或用户明确提供的来源");
      }
      const sources = [];
      const registry: GovernedSubagentSource[] = [];
      for (const item of normalizedItems) {
        const source = await openWebSource(item.url, {
          title: item.title,
          maxPages: params.max_pages,
          maxChars: params.max_chars,
        });
        if (!source.url) throw new Error("公开来源正文缺少规范 URL");
        sources.push(source);
        registry.push({
          source_id: source.source_id,
          title: source.title.slice(0, 500),
          source_type: source.source_type,
          url: source.url,
          content_sha256: source.content_sha256,
          accessed_at: source.accessed_at,
          ...(source.published_date ? { published_date: source.published_date } : {}),
          extraction_reliability: source.extraction_reliability,
        });
      }
      recordGovernedSources(projectRoot, params.contract_id, registry);
      const result = { opened_at: new Date().toISOString(), sources };
      return { content: resultContent(result), details: result };
    },
  });
}


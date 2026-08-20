import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { normalizePublicUrl, openWebSource } from "./source-readers.ts";
import { assertSafePublicQuery, searchPublicWeb } from "./web-search.ts";
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

export const assertSafePublicQueryForTests = assertSafePublicQuery;

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
    description: "仅供受管只读研究 Subagent 使用。按场景检索公开来源并登记可读取 URL；候选摘要不能替代正文证据。",
    parameters: Type.Object({
      contract_id: Type.String({ pattern: SAFE_CONTRACT_ID }),
      queries: Type.Array(Type.String({ minLength: 1, maxLength: 400 }), { minItems: 1, maxItems: 6, uniqueItems: true }),
      count: Type.Optional(Type.Number({ minimum: 1, maximum: 8 })),
      country: Type.Optional(Type.String({ pattern: "^[A-Za-z]{2}$" })),
      search_lang: Type.Optional(Type.String({ pattern: "^[A-Za-z-]{2,10}$" })),
      mode: Type.Optional(Type.Union([
        Type.Literal("auto"), Type.Literal("broad"), Type.Literal("official"),
        Type.Literal("chinese_policy"), Type.Literal("recent"),
      ])),
      site: Type.Optional(Type.String({ minLength: 3, maxLength: 253 })),
      published_after: Type.Optional(Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" })),
      published_before: Type.Optional(Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" })),
      snippet_chars: Type.Optional(Type.Number({ minimum: 180, maximum: 1200 })),
    }),
    async execute(_toolCallId, params) {
      requireContract(projectRoot, params.contract_id, "web.search");
      const { contract_id: _contractId, ...searchParams } = params;
      const result = await searchPublicWeb(searchParams);
      const registeredUrls = result.searches.flatMap((search) => search.results.map((item) => item.url));
      recordGovernedSearchUrls(projectRoot, params.contract_id, registeredUrls);
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

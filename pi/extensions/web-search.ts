import { normalizePublicUrl } from "./source-readers.ts";
import { lookup } from "node:dns/promises";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { isIP } from "node:net";
import { Readable } from "node:stream";

const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const SEARCH_TIMEOUT_MS = 60_000;
const DEFAULT_SNIPPET_CHARS = 600;
const KEENABLE_PUBLIC_ENDPOINT = "https://api.keenable.ai/v1/search/public";
const BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search";

export type PublicSearchMode = "auto" | "broad" | "official" | "chinese_policy" | "recent";
type SearchProvider = "brave" | "keenable-public" | "one-search";
type OneSearchMode = "parallel" | "fallback" | "single";
type NetworkTarget = { address: string; family: 4 | 6 };

export type PublicSearchParams = {
  queries: string[];
  count?: number;
  country?: string;
  search_lang?: string;
  mode?: PublicSearchMode;
  site?: string;
  published_after?: string;
  published_before?: string;
  snippet_chars?: number;
};

export type PublicSearchResult = {
  title: string;
  url: string;
  description: string;
  source_category_hint: "government" | "academic" | "other";
  age?: string;
  published_at?: string;
  acquired_at?: string;
  upstream_provider?: string;
};

export type PublicSearchResponse = {
  provider: SearchProvider;
  requested_mode: PublicSearchMode;
  mode: Exclude<PublicSearchMode, "auto">;
  searched_at: string;
  evidence_status: "discovery_only";
  applied_filters: {
    site?: string;
    published_after?: string;
    published_before?: string;
    acquired_after?: "30d";
    count: number;
    snippet_chars: number;
  };
  searches: Array<{ query: string; provider: SearchProvider; results: PublicSearchResult[] }>;
};

export function assertSafePublicQuery(value: string): string {
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

function positiveInteger(value: number | undefined, fallback: number, minimum: number, maximum: number, label: string): number {
  const normalized = value ?? fallback;
  if (!Number.isInteger(normalized) || normalized < minimum || normalized > maximum) {
    throw new Error(`${label}必须是 ${minimum}-${maximum} 的整数`);
  }
  return normalized;
}

function optionalDate(value: string | undefined, label: string): string | undefined {
  if (value === undefined) return undefined;
  const normalized = value.trim();
  const parsed = new Date(`${normalized}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(normalized) || Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== normalized) {
    throw new Error(`${label}必须为有效的 YYYY-MM-DD 日期`);
  }
  return normalized;
}

function optionalSite(value: string | undefined): string | undefined {
  if (value === undefined) return undefined;
  const site = value.trim().toLocaleLowerCase("en-US").replace(/^\.+/u, "");
  if (
    !site || site.length > 253 || site.includes(":") || site.includes("/") || site.includes("@")
    || isIP(site) !== 0 || site === "localhost" || !site.includes(".")
    || !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/u.test(site)
  ) {
    throw new Error("限定站点必须是普通公网域名，例如 gov.cn");
  }
  const parsed = normalizePublicUrl(`https://${site}/`);
  if (parsed.hostname !== site) {
    throw new Error("限定站点必须是普通公网域名，例如 gov.cn");
  }
  return site;
}

function providerOverride(): "auto" | SearchProvider {
  const configured = (process.env.DIRECTOR_SEARCH_PROVIDER ?? "auto").trim().toLocaleLowerCase("en-US");
  if (configured === "auto" || configured === "brave" || configured === "keenable-public" || configured === "one-search") return configured;
  throw new Error("DIRECTOR_SEARCH_PROVIDER 仅支持 auto、brave、keenable-public 或 one-search");
}

function chooseProvider(mode: PublicSearchMode, hasBraveKey: boolean, constrained: boolean, hasGateway: boolean): SearchProvider {
  const override = providerOverride();
  if (override === "one-search") {
    if (!hasGateway) throw new Error("已指定使用 One Search，但本机尚未完成搜索聚合网关配置");
    return "one-search";
  }
  if (override === "brave") {
    if (!hasBraveKey) throw new Error("已指定使用 Brave，但本机尚未配置 BRAVE_SEARCH_API_KEY");
    return "brave";
  }
  if (override === "keenable-public") return "keenable-public";
  if (hasGateway) return "one-search";
  if (mode === "chinese_policy" || constrained || !hasBraveKey) return "keenable-public";
  return "brave";
}

function effectiveMode(requested: PublicSearchMode, queries: string[]): Exclude<PublicSearchMode, "auto"> {
  if (requested !== "auto") return requested;
  const joined = queries.join(" ");
  if (/(?:政策|政府|政务|条例|规划|行动方案|实施方案|工信部|发改委|科技厅|gov\.cn)/iu.test(joined)) {
    return "chinese_policy";
  }
  if (/(?:最新|近期|本周|今日|新闻|动态|recent|latest|today|news)/iu.test(joined)) return "recent";
  if (/(?:标准|规范|白皮书|论文|研究报告|专利|standard|specification|paper|journal|arxiv)/iu.test(joined)) {
    return "official";
  }
  return "broad";
}

async function readBoundedJson(response: Response): Promise<unknown> {
  const contentType = (response.headers.get("Content-Type") ?? "").split(";", 1)[0]?.trim().toLocaleLowerCase("en-US");
  if (contentType !== "application/json" && contentType !== "text/json") {
    throw new Error("公开检索没有返回 JSON");
  }
  const declared = Number(response.headers.get("Content-Length") ?? "0");
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) {
    throw new Error("公开检索响应超过 2 MiB 安全上限");
  }
  if (!response.body) throw new Error("公开检索返回了空响应");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!value) continue;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel().catch(() => undefined);
        throw new Error("公开检索响应超过 2 MiB 安全上限");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = Buffer.concat(chunks.map((chunk) => Buffer.from(chunk))).toString("utf8");
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new Error("公开检索返回的内容不是有效 JSON");
  }
}

function categoryHint(url: URL): PublicSearchResult["source_category_hint"] {
  const host = url.hostname.toLocaleLowerCase("en-US");
  if (host.endsWith(".gov.cn") || host.endsWith(".gov") || host.endsWith(".gov.uk")) return "government";
  if (
    host.endsWith(".edu") || host.endsWith(".edu.cn") || host.endsWith(".ac.uk")
    || host === "arxiv.org" || host.endsWith(".arxiv.org") || host === "doi.org"
  ) return "academic";
  return "other";
}

function normalizedResult(input: {
  title?: unknown;
  url?: unknown;
  description?: unknown;
  snippet?: unknown;
  age?: unknown;
  published_at?: unknown;
  acquired_at?: unknown;
  content?: unknown;
  provider?: unknown;
  source?: unknown;
}, snippetChars: number): PublicSearchResult | undefined {
  if (typeof input.title !== "string" || typeof input.url !== "string") return undefined;
  try {
    const parsed = normalizePublicUrl(input.url);
    const description = typeof input.snippet === "string" && input.snippet.trim()
      ? input.snippet
      : typeof input.description === "string" && input.description.trim()
        ? input.description
        : typeof input.content === "string" ? input.content : "";
    const upstreamProvider = typeof input.provider === "string" && input.provider.trim()
      ? input.provider.trim()
      : typeof input.source === "string" ? input.source.trim() : "";
    return {
      title: input.title.trim().slice(0, 500),
      url: parsed.toString().slice(0, 2048),
      description: description.trim().slice(0, snippetChars),
      source_category_hint: categoryHint(parsed),
      ...(typeof input.age === "string" && input.age ? { age: input.age.slice(0, 100) } : {}),
      ...(typeof input.published_at === "string" && input.published_at ? { published_at: input.published_at.slice(0, 100) } : {}),
      ...(typeof input.acquired_at === "string" && input.acquired_at ? { acquired_at: input.acquired_at.slice(0, 100) } : {}),
      ...(upstreamProvider ? { upstream_provider: upstreamProvider.slice(0, 120) } : {}),
    };
  } catch {
    return undefined;
  }
}

function privateIpv4Kind(address: string): "allowed" | "blocked" | "public" {
  const parts = address.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return "blocked";
  const [a, b] = parts;
  if (
    a === 10 || a === 127 || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)
  ) return "allowed";
  if (
    a === 0 || a >= 224 || (a === 100 && b >= 64 && b <= 127)
    || (a === 192 && b === 0) || (a === 192 && b === 88 && parts[2] === 99)
    || (a === 198 && (b === 18 || b === 19 || (b === 51 && parts[2] === 100)))
    || (a === 203 && b === 0 && parts[2] === 113)
  ) return "blocked";
  return "public";
}

function gatewayAddressKind(address: string): "allowed-private" | "blocked" | "public" {
  const version = isIP(address);
  if (version === 4) {
    const kind = privateIpv4Kind(address);
    return kind === "allowed" ? "allowed-private" : kind;
  }
  if (version !== 6) return "blocked";
  const normalized = address.toLowerCase();
  if (normalized.startsWith("::ffff:")) {
    const kind = privateIpv4Kind(normalized.slice(7));
    return kind === "allowed" ? "allowed-private" : kind;
  }
  if (normalized === "::1" || normalized.startsWith("fc") || normalized.startsWith("fd") || normalized.startsWith("fe8") || normalized.startsWith("fe9") || normalized.startsWith("fea") || normalized.startsWith("feb")) return "allowed-private";
  if (normalized === "::" || normalized.startsWith("ff") || normalized.startsWith("2001:db8:") || normalized === "2001:db8::") return "blocked";
  const first = Number.parseInt(normalized.split(":")[0] || "0", 16);
  return Number.isFinite(first) && first >= 0x2000 && first <= 0x3fff ? "public" : "blocked";
}

async function resolveGatewayTarget(url: URL, allowPrivate: boolean): Promise<NetworkTarget> {
  const hostname = url.hostname.replace(/^\[|\]$/gu, "").toLowerCase();
  let raw: Array<{ address: string; family: number }>;
  if (isIP(hostname)) raw = [{ address: hostname, family: isIP(hostname) }];
  else {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      raw = await Promise.race([
        lookup(hostname, { all: true, verbatim: true }),
        new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("One Search 网关域名解析超时")), 10_000); }),
      ]);
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  if (!raw.length) throw new Error("One Search 网关域名没有可用地址");
  const targets = raw.map((item) => ({ address: item.address, family: item.family as 4 | 6, kind: gatewayAddressKind(item.address) }));
  if (targets.some((target) => target.family !== 4 && target.family !== 6) || targets.some((target) => target.kind === "blocked")) {
    throw new Error("One Search 网关解析到未指定、多播或保留地址，已拒绝连接");
  }
  if (!allowPrivate && targets.some((target) => target.kind === "allowed-private")) {
    throw new Error("One Search 网关指向本机或局域网，但运行时未允许私网连接");
  }
  const selected = targets[0]!;
  return { address: selected.address, family: selected.family };
}

function oneSearchConfiguration(): {
  base: URL;
  token: string;
  mode: OneSearchMode;
  maxResults: number;
  allowPrivate: boolean;
  providers: string[];
} | undefined {
  const baseValue = process.env.ONE_SEARCH_BASE_URL?.trim() ?? "";
  const token = process.env.ONE_SEARCH_API_TOKEN?.trim() ?? "";
  if (!baseValue && !token) return undefined;
  if (!baseValue || token.length <= 4 || !token.startsWith("osr_") || token.startsWith("oak_")) throw new Error("One Search 配置不完整或令牌权限不合规；请在设置中重新保存 osr_ 检索令牌");
  let base: URL;
  try { base = new URL(baseValue); } catch { throw new Error("One Search 网关地址无效"); }
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error("One Search 网关地址必须是无凭据、无查询参数的 http/https 地址");
  }
  base.pathname = base.pathname.replace(/\/$/u, "").replace(/\/v1$/u, "");
  const mode = (process.env.ONE_SEARCH_MODE ?? "parallel") as OneSearchMode;
  if (!["parallel", "fallback", "single"].includes(mode)) throw new Error("One Search 聚合方式无效");
  const maxResults = Number(process.env.ONE_SEARCH_MAX_RESULTS ?? "8");
  if (!Number.isInteger(maxResults) || maxResults < 1 || maxResults > 10) throw new Error("One Search 结果数配置必须是 1–10 的整数");
  const providersConfigured = process.env.ONE_SEARCH_PROVIDERS !== undefined;
  let providers: unknown;
  try {
    providers = JSON.parse(process.env.ONE_SEARCH_PROVIDERS ?? "[]") as unknown;
  } catch {
    throw new Error("One Search 搜索来源配置不是有效 JSON");
  }
  if (
    !Array.isArray(providers) || providers.length > 20
    || providers.some((provider) => typeof provider !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/u.test(provider))
    || new Set(providers).size !== providers.length
  ) {
    throw new Error("One Search 搜索来源配置无效");
  }
  if (providersConfigured && mode === "single" && providers.length !== 1) {
    throw new Error("One Search 单一来源模式必须且只能配置一个搜索来源");
  }
  return {
    base, token, mode, maxResults,
    allowPrivate: process.env.ONE_SEARCH_ALLOW_PRIVATE_NETWORK === "1",
    providers,
  };
}

function pinnedGatewayRequest(url: URL, target: NetworkTarget, token: string, body: string, signal: AbortSignal): Promise<Response> {
  return new Promise((resolvePromise, reject) => {
    const transport = url.protocol === "https:" ? httpsRequest : httpRequest;
    const tlsHostname = url.hostname.replace(/^\[|\]$/gu, "");
    const request = transport(url, {
      method: "POST",
      headers: {
        Accept: "application/json", "Content-Type": "application/json",
        Authorization: `Bearer ${token}`, "Content-Length": Buffer.byteLength(body),
      },
      lookup: ((_hostname: string, options: { all?: boolean } | number, callback: ((error: NodeJS.ErrnoException | null, address: string, family: number) => void) | ((error: NodeJS.ErrnoException | null, addresses: NetworkTarget[]) => void)) => {
        if (typeof options === "object" && options.all) (callback as (error: NodeJS.ErrnoException | null, addresses: NetworkTarget[]) => void)(null, [target]);
        else (callback as (error: NodeJS.ErrnoException | null, address: string, family: number) => void)(null, target.address, target.family);
      }) as never,
      servername: url.protocol === "https:" && !isIP(tlsHostname) ? tlsHostname : undefined,
      signal,
    }, (incoming) => {
      const status = incoming.statusCode ?? 0;
      const headers = new Headers();
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (Array.isArray(value)) value.forEach((item) => headers.append(name, item));
        else if (value !== undefined) headers.set(name, value);
      }
      resolvePromise(new Response(Readable.toWeb(incoming) as ReadableStream<Uint8Array>, { status, headers }));
    });
    request.once("error", reject);
    request.end(body);
  });
}

async function oneSearch(
  query: string,
  count: number,
  snippetChars: number,
  configuration: NonNullable<ReturnType<typeof oneSearchConfiguration>>,
  signal: AbortSignal,
): Promise<PublicSearchResult[]> {
  const endpoint = new URL(`${configuration.base.toString().replace(/\/$/u, "")}/v1/search`);
  const target = await resolveGatewayTarget(endpoint, configuration.allowPrivate);
  if (endpoint.protocol === "http:" && gatewayAddressKind(target.address) === "public") {
    throw new Error("公网 One Search 网关必须使用 HTTPS");
  }
  const body = JSON.stringify({
    query,
    mode: configuration.mode,
    limit: Math.min(count, configuration.maxResults),
    dedupe: true,
    ...(configuration.providers.length ? { providers: configuration.providers } : {}),
  });
  const response = await pinnedGatewayRequest(endpoint, target, configuration.token, body, signal);
  if (!response.ok) throw new Error(`One Search 聚合检索失败（HTTP ${response.status}）；请检查网关状态、osr_ 令牌和提供商配置`);
  const payload = await readBoundedJson(response) as { results?: Array<Record<string, unknown>>; data?: { results?: Array<Record<string, unknown>> } };
  const results = payload.results ?? payload.data?.results ?? [];
  return results.slice(0, Math.min(count, configuration.maxResults))
    .flatMap((item) => normalizedResult(item, snippetChars) ?? []);
}

function rankResults(results: PublicSearchResult[], mode: PublicSearchMode): PublicSearchResult[] {
  if (mode !== "official" && mode !== "chinese_policy") return results;
  const score = (result: PublicSearchResult) => {
    if (mode === "chinese_policy") return result.source_category_hint === "government" ? 0 : 1;
    if (result.source_category_hint === "government") return 0;
    if (result.source_category_hint === "academic") return 1;
    return 2;
  };
  return results.map((result, index) => ({ result, index }))
    .sort((left, right) => score(left.result) - score(right.result) || left.index - right.index)
    .map(({ result }) => result);
}

async function braveSearch(
  query: string,
  key: string,
  count: number,
  snippetChars: number,
  country: string | undefined,
  searchLang: string | undefined,
  signal: AbortSignal,
): Promise<PublicSearchResult[]> {
  const endpoint = new URL(BRAVE_ENDPOINT);
  endpoint.searchParams.set("q", query);
  endpoint.searchParams.set("count", String(count));
  if (country) endpoint.searchParams.set("country", country.toUpperCase());
  if (searchLang) endpoint.searchParams.set("search_lang", searchLang.toLowerCase());
  const response = await fetch(endpoint, {
    headers: { Accept: "application/json", "X-Subscription-Token": key },
    signal,
    redirect: "error",
  });
  if (!response.ok) throw new Error(`Brave 公开检索失败（HTTP ${response.status}）；请检查专用密钥、配额或网络`);
  const payload = await readBoundedJson(response) as {
    web?: { results?: Array<{ title?: unknown; url?: unknown; description?: unknown; age?: unknown }> };
  };
  return (payload.web?.results ?? []).slice(0, count)
    .flatMap((item) => normalizedResult(item, snippetChars) ?? []);
}

async function keenableSearch(
  query: string,
  count: number,
  snippetChars: number,
  mode: PublicSearchMode,
  site: string | undefined,
  publishedAfter: string | undefined,
  publishedBefore: string | undefined,
  acquiredAfter: "30d" | undefined,
  signal: AbortSignal,
): Promise<PublicSearchResult[]> {
  const body = {
    query,
    mode: mode === "recent" ? "realtime" : "pro",
    max_results: count,
    snippet_max_length: Math.max(180, snippetChars),
    ...(site ? { site } : {}),
    ...(publishedAfter ? { published_after: publishedAfter } : {}),
    ...(publishedBefore ? { published_before: publishedBefore } : {}),
    ...(acquiredAfter ? { acquired_after: acquiredAfter } : {}),
  };
  const response = await fetch(KEENABLE_PUBLIC_ENDPOINT, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-Keenable-Title": "Agent4Market",
    },
    body: JSON.stringify(body),
    signal,
    redirect: "error",
  });
  if (!response.ok) {
    const suffix = response.status === 429 ? "；共享公共额度繁忙，可稍后重试或配置专用 Brave 密钥" : "";
    throw new Error(`免密公开检索失败（HTTP ${response.status}）${suffix}`);
  }
  const payload = await readBoundedJson(response) as {
    results?: Array<{
      title?: unknown;
      url?: unknown;
      description?: unknown;
      snippet?: unknown;
      published_at?: unknown;
      acquired_at?: unknown;
    }>;
  };
  return (payload.results ?? []).slice(0, count)
    .flatMap((item) => normalizedResult(item, snippetChars) ?? []);
}

export async function searchPublicWeb(params: PublicSearchParams): Promise<PublicSearchResponse> {
  const queries = params.queries.map(assertSafePublicQuery);
  const normalizedQueries = queries.map((query) => query.toLocaleLowerCase("zh-CN"));
  if (new Set(normalizedQueries).size !== normalizedQueries.length) throw new Error("同一批公开检索不能包含重复词");
  const count = positiveInteger(params.count, 8, 1, 10, "每个查询的结果数");
  const snippetChars = positiveInteger(params.snippet_chars, DEFAULT_SNIPPET_CHARS, 180, 1200, "摘要长度");
  const requestedMode = params.mode ?? "auto";
  if (!["auto", "broad", "official", "chinese_policy", "recent"].includes(requestedMode)) {
    throw new Error("公开检索场景无效");
  }
  const mode = effectiveMode(requestedMode, queries);
  const site = optionalSite(params.site) ?? (mode === "chinese_policy" ? "gov.cn" : undefined);
  const publishedAfter = optionalDate(params.published_after, "发布日期起点");
  const publishedBefore = optionalDate(params.published_before, "发布日期终点");
  if (publishedAfter && publishedBefore && publishedAfter > publishedBefore) {
    throw new Error("发布日期起点不能晚于终点");
  }
  const acquiredAfter = mode === "recent" && !publishedAfter ? "30d" : undefined;
  const key = process.env.BRAVE_SEARCH_API_KEY?.trim() ?? "";
  const gateway = oneSearchConfiguration();
  const provider = chooseProvider(mode, Boolean(key), Boolean(site || publishedAfter || publishedBefore || acquiredAfter), Boolean(gateway));
  const effectiveCount = provider === "one-search" ? Math.min(count, gateway!.maxResults) : count;
  const signal = AbortSignal.timeout(SEARCH_TIMEOUT_MS);
  const seenUrls = new Set<string>();
  const searches: PublicSearchResponse["searches"] = [];
  for (const query of queries) {
    const filteredQuery = provider === "one-search"
      ? [query, site ? `site:${site}` : "", publishedAfter ? `after:${publishedAfter}` : "", publishedBefore ? `before:${publishedBefore}` : ""].filter(Boolean).join(" ")
      : query;
    const rawResults = provider === "brave"
      ? await braveSearch(filteredQuery, key, effectiveCount, snippetChars, params.country, params.search_lang, signal)
      : provider === "one-search"
        ? await oneSearch(filteredQuery, effectiveCount, snippetChars, gateway!, signal)
        : await keenableSearch(filteredQuery, effectiveCount, snippetChars, mode, site, publishedAfter, publishedBefore, acquiredAfter, signal);
    const results = rankResults(rawResults, mode).filter((result) => {
      if (seenUrls.has(result.url)) return false;
      seenUrls.add(result.url);
      return true;
    });
    searches.push({ query, provider, results });
  }
  return {
    provider,
    requested_mode: requestedMode,
    mode,
    searched_at: new Date().toISOString(),
    evidence_status: "discovery_only",
    applied_filters: {
      ...(site ? { site } : {}),
      ...(publishedAfter ? { published_after: publishedAfter } : {}),
      ...(publishedBefore ? { published_before: publishedBefore } : {}),
      ...(acquiredAfter ? { acquired_after: acquiredAfter } : {}),
      count: effectiveCount,
      snippet_chars: snippetChars,
    },
    searches,
  };
}

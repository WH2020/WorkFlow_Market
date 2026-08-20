import {
  closeSync,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { acquireTaskLock, releaseTaskLock } from "./task-runtime.ts";

const CONTRACT_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/u;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/u;
const MAX_CONTRACT_BYTES = 1024 * 1024;
const MAX_RESULT_BYTES = 512 * 1024;
const ALLOWED_LOGICAL_TOOLS = new Set(["web.search", "web.open"]);

export type GovernedSubagentRole = "research-scout" | "readonly-reviewer";

export type GovernedSubagentSource = {
  source_id: string;
  title: string;
  source_type: string;
  url: string;
  content_sha256: string;
  accessed_at: string;
  published_date?: string;
  extraction_reliability?: string;
  knowledge_mutation?: {
    operation: "insert";
    record_id: string;
    changes: Record<string, string>;
  };
};

export type GovernedSubagentContract = {
  schema_version: "1.0";
  contract_id: string;
  task_id: string;
  profile_id: string;
  node_id: string;
  task_version: number;
  role: GovernedSubagentRole;
  objective: string;
  allowed_tools: string[];
  authorized_urls: string[];
  searched_urls: string[];
  sources: GovernedSubagentSource[];
  revision: number;
  created_at: string;
  expires_at: string;
};

export type GovernedSubagentResult = {
  schema_version: "1.0";
  contract_id: string;
  task_id: string;
  profile_id: string;
  node_id: string;
  role: GovernedSubagentRole;
  agent: string;
  model?: string;
  run_id?: string;
  output: string;
  sources: GovernedSubagentSource[];
  contract_sha256: string;
  output_sha256: string;
  receipt_sha256: string;
  completed_at: string;
};

function isContained(root: string, candidate: string): boolean {
  const rel = relative(root, candidate);
  return rel === "" || (!isAbsolute(rel) && rel !== ".." && !rel.startsWith(`..${sep}`));
}

function canonical(value: unknown): string {
  const normalize = (item: unknown): unknown => {
    if (item === null || typeof item === "string" || typeof item === "boolean") return item;
    if (typeof item === "number" && Number.isFinite(item)) return item;
    if (Array.isArray(item)) return item.map(normalize);
    if (item && typeof item === "object") {
      return Object.fromEntries(
        Object.entries(item as Record<string, unknown>)
          .filter(([, child]) => child !== undefined)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, child]) => [key, normalize(child)]),
      );
    }
    throw new Error("Subagent receipt contains non-finite JSON data");
  };
  return JSON.stringify(normalize(value));
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function runtimeDirectory(projectRoot: string, name: "subagent-contracts" | "subagent-results"): string {
  const project = realpathSync.native(resolve(projectRoot));
  const runtime = resolve(project, ".pi", "director-runtime", name);
  mkdirSync(runtime, { recursive: true, mode: 0o700 });
  const meta = lstatSync(runtime);
  if (!meta.isDirectory() || meta.isSymbolicLink()) throw new Error(`Subagent ${name} directory is not a regular directory`);
  const actual = realpathSync.native(runtime);
  if (!isContained(project, actual)) throw new Error(`Subagent ${name} directory escapes the project`);
  return actual;
}

function validateContractId(contractId: string): void {
  if (!CONTRACT_ID.test(contractId)) throw new Error("Invalid governed subagent contract id");
}

function contractPath(projectRoot: string, contractId: string): string {
  validateContractId(contractId);
  return join(runtimeDirectory(projectRoot, "subagent-contracts"), `${contractId}.json`);
}

function assertGovernedSource(source: GovernedSubagentSource): void {
  if (
    !source || typeof source !== "object" ||
    !SAFE_ID.test(source.source_id) ||
    typeof source.title !== "string" || !source.title.trim() || source.title.length > 500 ||
    typeof source.url !== "string" || source.url.length > 2048 ||
    typeof source.source_type !== "string" || !source.source_type.trim() || source.source_type.length > 80 ||
    !/^[a-f0-9]{64}$/u.test(source.content_sha256) ||
    typeof source.accessed_at !== "string" || !Number.isFinite(Date.parse(source.accessed_at)) ||
    (source.published_date !== undefined && (
      typeof source.published_date !== "string" || !Number.isFinite(Date.parse(source.published_date))
    )) ||
    (source.extraction_reliability !== undefined &&
      source.extraction_reliability !== "standard" && source.extraction_reliability !== "limited")
  ) throw new Error("Governed subagent source registry is invalid");
  if (source.knowledge_mutation !== undefined) {
    const mutation = source.knowledge_mutation;
    if (
      !mutation || mutation.operation !== "insert" || mutation.record_id !== source.source_id ||
      !mutation.changes || typeof mutation.changes !== "object" || Array.isArray(mutation.changes) ||
      Object.keys(mutation.changes).length > 100 ||
      Object.values(mutation.changes).some((entry) => typeof entry !== "string") ||
      mutation.changes.title !== source.title || mutation.changes.url !== source.url ||
      !new RegExp(`(?:^|;\\s*)content_sha256=${source.content_sha256}(?:;|$)`, "u").test(mutation.changes.notes ?? "")
    ) throw new Error("Governed subagent knowledge mutation is invalid");
  }
}

function assertContract(value: unknown, expectedId: string): GovernedSubagentContract {
  if (!value || typeof value !== "object") throw new Error("Governed subagent contract must be an object");
  const item = value as Partial<GovernedSubagentContract>;
  if (
    item.schema_version !== "1.0" ||
    item.contract_id !== expectedId ||
    !SAFE_ID.test(item.task_id ?? "") ||
    !SAFE_ID.test(item.profile_id ?? "") ||
    !SAFE_ID.test(item.node_id ?? "") ||
    !Number.isInteger(item.task_version) ||
    (item.task_version ?? 0) < 1 ||
    (item.role !== "research-scout" && item.role !== "readonly-reviewer") ||
    typeof item.objective !== "string" ||
    !item.objective.trim() ||
    item.objective.length > 2000 ||
    !Array.isArray(item.allowed_tools) ||
    item.allowed_tools.some((tool) => typeof tool !== "string" || !ALLOWED_LOGICAL_TOOLS.has(tool)) ||
    !Array.isArray(item.authorized_urls) ||
    !Array.isArray(item.searched_urls) ||
    !Array.isArray(item.sources) ||
    !Number.isInteger(item.revision) ||
    (item.revision ?? -1) < 0 ||
    typeof item.created_at !== "string" ||
    !Number.isFinite(Date.parse(item.created_at)) ||
    typeof item.expires_at !== "string" ||
    !Number.isFinite(Date.parse(item.expires_at))
  ) {
    throw new Error("Governed subagent contract has an invalid schema");
  }
  if (new Set(item.allowed_tools).size !== item.allowed_tools.length) throw new Error("Governed subagent tool list contains duplicates");
  if (item.role === "readonly-reviewer" && item.allowed_tools.length !== 0) throw new Error("Reviewer subagent cannot receive tools");
  if (item.authorized_urls.some((url) => typeof url !== "string" || url.length > 2048)) throw new Error("Governed subagent authorized URL is invalid");
  if (item.searched_urls.some((url) => typeof url !== "string" || url.length > 2048)) throw new Error("Governed subagent searched URL is invalid");
  if (item.sources.length > 60) throw new Error("Governed subagent source registry exceeds 60 items");
  for (const source of item.sources) assertGovernedSource(source);
  if (new Set(item.sources.map((source) => source.source_id)).size !== item.sources.length) {
    throw new Error("Governed subagent source registry contains duplicate IDs");
  }
  return item as GovernedSubagentContract;
}

function readBoundedJson(path: string, expectedId: string): GovernedSubagentContract {
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink() || meta.size > MAX_CONTRACT_BYTES) throw new Error("Governed subagent contract file is invalid");
  const directory = realpathSync.native(dirname(path));
  const actual = realpathSync.native(path);
  if (!isContained(directory, actual)) throw new Error("Governed subagent contract path escapes its directory");
  return assertContract(JSON.parse(readFileSync(actual, "utf8")), expectedId);
}

function atomicJson(path: string, value: unknown): void {
  const serialized = `${JSON.stringify(value, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > MAX_CONTRACT_BYTES) throw new Error("Governed subagent state exceeds 1 MiB");
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, "wx", 0o600);
    writeFileSync(descriptor, serialized, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, path);
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
    if (existsSync(temporary)) rmSync(temporary, { force: true });
  }
}

export function createGovernedSubagentContract(
  projectRoot: string,
  input: Omit<GovernedSubagentContract, "schema_version" | "contract_id" | "searched_urls" | "sources" | "revision" | "created_at" | "expires_at">,
  ttlMs = 15 * 60 * 1000,
): GovernedSubagentContract {
  if (!Number.isInteger(ttlMs) || ttlMs < 60_000 || ttlMs > 60 * 60 * 1000) throw new Error("Governed subagent contract TTL is invalid");
  const created = new Date();
  const contractId = randomUUID();
  const contract = assertContract({
    schema_version: "1.0",
    contract_id: contractId,
    ...input,
    authorized_urls: [...new Set(input.authorized_urls)].sort(),
    allowed_tools: [...new Set(input.allowed_tools)],
    searched_urls: [],
    sources: [],
    revision: 0,
    created_at: created.toISOString(),
    expires_at: new Date(created.getTime() + ttlMs).toISOString(),
  }, contractId);
  const validated = assertContract(contract, contractId);
  const path = contractPath(projectRoot, validated.contract_id);
  let descriptor: number | undefined;
  try {
    descriptor = openSync(path, "wx", 0o600);
    writeFileSync(descriptor, `${JSON.stringify(validated, null, 2)}\n`, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
  return validated;
}

export function loadGovernedSubagentContract(projectRoot: string, contractId: string): GovernedSubagentContract {
  const contract = readBoundedJson(contractPath(projectRoot, contractId), contractId);
  if (Date.parse(contract.expires_at) < Date.now()) throw new Error("Governed subagent contract has expired");
  return contract;
}

export function updateGovernedSubagentContract(
  projectRoot: string,
  contractId: string,
  mutate: (contract: GovernedSubagentContract) => GovernedSubagentContract,
): GovernedSubagentContract {
  const path = contractPath(projectRoot, contractId);
  const lockPath = `${path}.lock`;
  let lock: number | undefined;
  try {
    lock = acquireTaskLock(lockPath);
    const current = loadGovernedSubagentContract(projectRoot, contractId);
    const proposed = mutate(JSON.parse(JSON.stringify(current)) as GovernedSubagentContract);
    const next = assertContract({ ...proposed, revision: current.revision + 1 }, contractId);
    if (
      next.task_id !== current.task_id ||
      next.profile_id !== current.profile_id ||
      next.node_id !== current.node_id ||
      next.task_version !== current.task_version ||
      next.role !== current.role ||
      canonical(next.allowed_tools) !== canonical(current.allowed_tools) ||
      canonical(next.authorized_urls) !== canonical(current.authorized_urls) ||
      next.created_at !== current.created_at ||
      next.expires_at !== current.expires_at
    ) throw new Error("Governed subagent contract attempted to change immutable fields");
    atomicJson(path, next);
    return next;
  } finally {
    if (lock !== undefined) releaseTaskLock(lockPath, lock);
  }
}

export function recordGovernedSearchUrls(projectRoot: string, contractId: string, urls: string[]): GovernedSubagentContract {
  return updateGovernedSubagentContract(projectRoot, contractId, (contract) => ({
    ...contract,
    searched_urls: [...new Set([...contract.searched_urls, ...urls])].sort().slice(0, 100),
  }));
}

export function recordGovernedSources(projectRoot: string, contractId: string, sources: GovernedSubagentSource[]): GovernedSubagentContract {
  return updateGovernedSubagentContract(projectRoot, contractId, (contract) => {
    const byId = new Map(contract.sources.map((source) => [source.source_id, source]));
    for (const source of sources) byId.set(source.source_id, source);
    return { ...contract, sources: [...byId.values()].sort((left, right) => left.source_id.localeCompare(right.source_id)).slice(0, 60) };
  });
}

export function removeGovernedSubagentContract(projectRoot: string, contractId: string): void {
  const path = contractPath(projectRoot, contractId);
  if (!existsSync(path)) return;
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink()) throw new Error("Refusing to remove an invalid governed subagent contract");
  rmSync(path, { force: true });
}

export function cleanupExpiredSubagentContracts(projectRoot: string, nowMs = Date.now()): number {
  const directory = runtimeDirectory(projectRoot, "subagent-contracts");
  let removed = 0;
  for (const name of readdirSync(directory).filter((candidate) => CONTRACT_ID.test(candidate.replace(/\.json$/u, "")) && candidate.endsWith(".json")).slice(0, 500)) {
    const path = join(directory, name);
    try {
      const id = name.slice(0, -5);
      const contract = readBoundedJson(path, id);
      if (Date.parse(contract.expires_at) >= nowMs) continue;
      const meta = statSync(path);
      if (meta.isFile()) {
        rmSync(path, { force: true });
        removed += 1;
      }
    } catch {
      // Invalid entries are preserved for manual inspection; never delete an unverified target.
    }
  }
  return removed;
}

export function writeGovernedSubagentResult(
  projectRoot: string,
  contract: GovernedSubagentContract,
  input: Pick<GovernedSubagentResult, "agent" | "model" | "run_id" | "output">,
): { result: GovernedSubagentResult; path: string } {
  const output = input.output.trim();
  if (!output || Buffer.byteLength(output, "utf8") > 256 * 1024) throw new Error("Governed subagent output must be 1-256 KiB");
  const base = {
    schema_version: "1.0" as const,
    contract_id: contract.contract_id,
    task_id: contract.task_id,
    profile_id: contract.profile_id,
    node_id: contract.node_id,
    role: contract.role,
    agent: input.agent,
    ...(input.model ? { model: input.model } : {}),
    ...(input.run_id ? { run_id: input.run_id } : {}),
    output,
    sources: contract.sources,
    contract_sha256: sha256(canonical(contract)),
    output_sha256: sha256(output),
    completed_at: new Date().toISOString(),
  };
  const result: GovernedSubagentResult = { ...base, receipt_sha256: sha256(canonical(base)) };
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  if (Buffer.byteLength(serialized, "utf8") > MAX_RESULT_BYTES) throw new Error("Governed subagent receipt exceeds 512 KiB");
  const directory = runtimeDirectory(projectRoot, "subagent-results");
  const path = join(directory, `${contract.contract_id}.json`);
  let descriptor: number | undefined;
  try {
    descriptor = openSync(path, "wx", 0o600);
    writeFileSync(descriptor, serialized, "utf8");
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
  return {
    result,
    path: relative(realpathSync.native(resolve(projectRoot)), path).replaceAll("\\", "/"),
  };
}

export function loadGovernedSubagentResult(projectRoot: string, contractId: string): GovernedSubagentResult {
  validateContractId(contractId);
  const path = join(runtimeDirectory(projectRoot, "subagent-results"), `${contractId}.json`);
  if (!existsSync(path)) throw new Error("Governed subagent result is missing");
  const meta = lstatSync(path);
  if (!meta.isFile() || meta.isSymbolicLink() || meta.size < 2 || meta.size > MAX_RESULT_BYTES) {
    throw new Error("Governed subagent result is not a bounded regular file");
  }
  const item = JSON.parse(readFileSync(path, "utf8")) as Partial<GovernedSubagentResult>;
  if (
    item.schema_version !== "1.0" || item.contract_id !== contractId ||
    !SAFE_ID.test(item.task_id ?? "") || !SAFE_ID.test(item.profile_id ?? "") || !SAFE_ID.test(item.node_id ?? "") ||
    (item.role !== "research-scout" && item.role !== "readonly-reviewer") ||
    typeof item.agent !== "string" || !item.agent.trim() || item.agent.length > 128 ||
    typeof item.output !== "string" || !item.output.trim() || Buffer.byteLength(item.output, "utf8") > 256 * 1024 ||
    !Array.isArray(item.sources) || item.sources.length > 60 ||
    !/^[a-f0-9]{64}$/u.test(item.contract_sha256 ?? "") ||
    !/^[a-f0-9]{64}$/u.test(item.output_sha256 ?? "") ||
    !/^[a-f0-9]{64}$/u.test(item.receipt_sha256 ?? "") ||
    typeof item.completed_at !== "string" || !Number.isFinite(Date.parse(item.completed_at)) ||
    (item.model !== undefined && (typeof item.model !== "string" || item.model.length > 256)) ||
    (item.run_id !== undefined && (typeof item.run_id !== "string" || item.run_id.length > 256))
  ) throw new Error("Governed subagent result has an invalid schema");
  for (const source of item.sources) assertGovernedSource(source);
  if (new Set(item.sources.map((source) => source.source_id)).size !== item.sources.length) {
    throw new Error("Governed subagent result contains duplicate source IDs");
  }
  if (item.output_sha256 !== sha256(item.output)) throw new Error("Governed subagent output hash mismatch");
  const { receipt_sha256: receiptSha256, ...base } = item as GovernedSubagentResult;
  if (receiptSha256 !== sha256(canonical(base))) throw new Error("Governed subagent receipt hash mismatch");
  return item as GovernedSubagentResult;
}

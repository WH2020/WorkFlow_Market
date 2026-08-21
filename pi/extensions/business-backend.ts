import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

import { BUSINESS_SCHEMA_VERSION, SalesBusinessStore } from "./business-store.ts";
import { LocalBusinessStoreError } from "./local-business-store.ts";

export type BusinessBackend =
  | { kind: "csv"; backend: "csv"; binding: string; binding_id: string }
  | { kind: "sqlite"; backend: "sqlite"; binding: string; binding_id: string; database_path: string; pointer_sha256: string };

function contained(root: string, candidate: string): boolean {
  const rel = relative(root, candidate);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function fail(code: string, message: string): never {
  throw new LocalBusinessStoreError(code, message);
}

function assertNoSymlinkComponents(root: string, candidate: string, label: string): void {
  const parts = relative(root, candidate).split(/[\\/]/u).filter(Boolean);
  let current = root;
  for (const part of parts) {
    current = resolve(current, part);
    if (existsSync(current) && lstatSync(current).isSymbolicLink()) fail("UNSAFE_PATH", `${label} 路径不能包含符号链接`);
  }
}

/** Resolve the only authoritative runtime storage selector. A database file alone never activates SQLite. */
export function resolveBusinessBackend(projectRoot: string): BusinessBackend {
  const root = realpathSync.native(resolve(projectRoot));
  const pointer = resolve(root, "data", "storage-backend.json");
  let pointerMeta;
  try {
    pointerMeta = lstatSync(pointer);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { kind: "csv", backend: "csv", binding: "csv:pointer-absent", binding_id: "csv:pointer-absent" };
    }
    throw error;
  }
  assertNoSymlinkComponents(root, pointer, "存储指针");
  if (!pointerMeta.isFile() || pointerMeta.isSymbolicLink()) fail("UNSAFE_POINTER", "存储指针必须是项目内普通文件，不能是符号链接");
  const canonicalPointer = realpathSync.native(pointer);
  if (!contained(root, canonicalPointer)) fail("UNSAFE_POINTER", "存储指针越出项目目录");
  if (pointerMeta.size < 2 || pointerMeta.size > 64 * 1024) fail("INVALID_POINTER", "存储指针大小无效");
  const bytes = readFileSync(canonicalPointer);
  let raw: unknown;
  try { raw = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { fail("INVALID_POINTER", "存储指针不是有效的 UTF-8 JSON"); }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("INVALID_POINTER", "存储指针必须是对象");
  const value = raw as Record<string, unknown>;
  if (value.backend !== "sqlite") fail("INVALID_POINTER", "存储指针 backend 只允许 sqlite；CSV 模式必须移除指针");
  if (value.schema_version !== BUSINESS_SCHEMA_VERSION) {
    fail("SCHEMA_UNSUPPORTED", `只支持 schema v${BUSINESS_SCHEMA_VERSION} 的存储指针`);
  }
  const relativePath = value.database_relative_path;
  if (
    typeof relativePath !== "string" || !relativePath || relativePath.length > 4096 ||
    isAbsolute(relativePath) || relativePath.includes("\\") || relativePath.includes("\0")
  ) {
    fail("INVALID_POINTER", "database_relative_path 必须是项目内相对路径");
  }
  if (relativePath.split("/").some((part) => part === ".." || part === "." || part === "")) {
    fail("UNSAFE_PATH", "database_relative_path 不能包含空段、当前目录或上级目录");
  }
  const requested = resolve(root, relativePath);
  if (!contained(root, requested) || !existsSync(requested)) fail("STORE_MISSING", "存储指针指向的 SQLite 数据库不存在");
  assertNoSymlinkComponents(root, requested, "SQLite 数据库");
  const databaseMeta = lstatSync(requested);
  if (!databaseMeta.isFile() || databaseMeta.isSymbolicLink()) fail("UNSAFE_PATH", "SQLite 数据库必须是项目内普通文件，不能是符号链接");
  const databasePath = realpathSync.native(requested);
  if (!contained(root, databasePath)) fail("UNSAFE_PATH", "SQLite 数据库越出项目目录");
  const pointerSha256 = createHash("sha256").update(bytes).digest("hex");
  const binding = `sqlite:v${BUSINESS_SCHEMA_VERSION}:${pointerSha256}`;
  return {
    kind: "sqlite",
    backend: "sqlite",
    binding,
    binding_id: binding,
    database_path: databasePath,
    pointer_sha256: pointerSha256,
  };
}

export function openBusinessStore(
  projectRoot: string,
  readOnly: boolean,
  expectedBinding?: { backend: "csv" | "sqlite"; binding_id: string },
): SalesBusinessStore | undefined {
  const backend = resolveBusinessBackend(projectRoot);
  if (expectedBinding && (expectedBinding.backend !== backend.backend || expectedBinding.binding_id !== backend.binding_id)) {
    fail("STORAGE_BINDING_CHANGED", "业务存储在审批后发生变化；拒绝打开不同的存储实例");
  }
  if (backend.kind === "csv") return undefined;
  return new SalesBusinessStore(backend.database_path, { read_only: readOnly, create_if_missing: false });
}

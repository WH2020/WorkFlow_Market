import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

import {
  LocalBusinessStore,
  LocalBusinessStoreError,
  SQLITE_GATE_MINIMUM_NODE,
  gatePayloadSha256,
} from "../extensions/local-business-store.ts";

const projectRoot = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const workerPath = fileURLToPath(new URL("./sqlite-driver-gate-worker.mjs", import.meta.url));
const startedAt = new Date().toISOString();
const checks = [];

function parseArguments(argv) {
  const result = { keep: false, report: undefined, expectArch: undefined };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--keep") result.keep = true;
    else if (value === "--report") result.report = argv[++index];
    else if (value === "--expect-arch") result.expectArch = argv[++index];
    else throw new Error(`Unknown argument: ${value}`);
  }
  if (result.report) result.report = resolve(result.report);
  return result;
}

function normalizedArchitecture(value) {
  const normalized = String(value).toLowerCase();
  if (["x86_64", "amd64", "x64"].includes(normalized)) return "x64";
  if (["aarch64", "arm64"].includes(normalized)) return "arm64";
  return normalized;
}

function runtimeVersion() {
  const match = process.versions.node.match(/^(\d+)\.(\d+)\.(\d+)/);
  if (!match) throw new Error(`Unrecognized Node version: ${process.versions.node}`);
  return match.slice(1).map(Number);
}

function atLeast(actual, minimum) {
  for (let index = 0; index < 3; index += 1) {
    if (actual[index] > minimum[index]) return true;
    if (actual[index] < minimum[index]) return false;
  }
  return true;
}

function mutation(intentId, operation, recordId, value, expectedVersion) {
  const withoutHash = {
    intent_id: intentId,
    operation,
    record_id: recordId,
    value,
    ...(expectedVersion === undefined ? {} : { expected_version: expectedVersion }),
  };
  return { ...withoutHash, payload_sha256: gatePayloadSha256(withoutHash) };
}

function safeWorkerEnvironment(request) {
  const allowed = ["HOME", "LOCALAPPDATA", "PATH", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "WINDIR"];
  const environment = {};
  for (const key of allowed) {
    if (process.env[key]) environment[key] = process.env[key];
  }
  environment.AGENT4MARKET_SQLITE_GATE_REQUEST = Buffer.from(JSON.stringify(request), "utf8").toString("base64url");
  return environment;
}

function runWorker(request, timeoutMs = 10_000) {
  return new Promise((resolvePromise, rejectPromise) => {
    const started = Date.now();
    const child = spawn(process.execPath, ["--no-warnings", workerPath], {
      cwd: projectRoot,
      env: safeWorkerEnvironment(request),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      rejectPromise(new Error(`SQLite gate worker exceeded ${timeoutMs} ms`));
    }, timeoutMs);
    child.once("error", (error) => {
      clearTimeout(timeout);
      rejectPromise(error);
    });
    child.once("close", (code, signal) => {
      clearTimeout(timeout);
      const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1);
      let json;
      if (line) {
        try { json = JSON.parse(line); } catch { json = undefined; }
      }
      resolvePromise({ code, signal, stdout, stderr, json, elapsed_ms: Date.now() - started });
    });
  });
}

async function runCheck(id, operation) {
  const start = process.hrtime.bigint();
  try {
    const details = await operation();
    checks.push({ id, status: "ok", duration_ms: Number(process.hrtime.bigint() - start) / 1_000_000, details });
  } catch (error) {
    checks.push({
      id,
      status: "error",
      duration_ms: Number(process.hrtime.bigint() - start) / 1_000_000,
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}

function assertStoreError(operation, expectedCode) {
  assert.throws(operation, (error) => error instanceof LocalBusinessStoreError && error.code === expectedCode);
}

function pythonCommand() {
  const candidates = process.platform === "win32"
    ? [["python"], ["py", "-3.11"]]
    : [["python3"], ["python"]];
  for (const candidate of candidates) {
    const probe = spawnSync(candidate[0], [...candidate.slice(1), "--version"], { encoding: "utf8", windowsHide: true });
    if (probe.status === 0) return candidate;
  }
  throw new Error("Python 3.11+ was not found for cross-language SQLite verification");
}

function writeReport(path, report) {
  if (!path) return;
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(report, null, 2)}\n`, { encoding: "utf8", flag: "w" });
}

const options = parseArguments(process.argv.slice(2));
const gateRoot = mkdtempSync(join(tmpdir(), "agent4market-sqlite-gate-"));
const primaryPath = join(gateRoot, "primary.db");
let finalStatus = "error";
let failure;

try {
  await runCheck("runtime", async () => {
    const actual = runtimeVersion();
    const minimum = SQLITE_GATE_MINIMUM_NODE.split(".").map(Number);
    assert.equal(actual[0], 24, `A0 candidate must use the pinned Node 24 LTS line, got ${process.versions.node}`);
    assert.ok(atLeast(actual, minimum), `A0 requires Node >=${SQLITE_GATE_MINIMUM_NODE}, got ${process.versions.node}`);
    const actualArch = normalizedArchitecture(process.arch);
    if (options.expectArch) assert.equal(actualArch, normalizedArchitecture(options.expectArch));
    return { node: process.versions.node, platform: process.platform, architecture: actualArch };
  });

  await runCheck("configuration", async () => {
    const store = new LocalBusinessStore(primaryPath, { timeout_ms: 5_000 });
    try {
      const configuration = store.configuration();
      assert.equal(configuration.journal_mode.toLowerCase(), "wal");
      assert.equal(configuration.synchronous, 2);
      assert.equal(configuration.foreign_keys, 1);
      assert.equal(configuration.busy_timeout, 5_000);
      assert.equal(configuration.integrity_check, "ok");
      return configuration;
    } finally {
      store.close();
    }
  });

  await runCheck("idempotent_receipt", async () => {
    const store = new LocalBusinessStore(primaryPath);
    try {
      const request = mutation("intent-idempotent", "insert", "record-idempotent", "first value");
      const first = store.commitMutation(request);
      for (let index = 0; index < 100; index += 1) {
        assert.deepEqual(store.commitMutation(request), first);
      }
      const changedPayload = mutation("intent-idempotent", "insert", "record-idempotent", "changed value");
      assertStoreError(() => store.commitMutation(changedPayload), "INTENT_PAYLOAD_CONFLICT");
      assert.deepEqual(store.counts(), { records: 1, receipts: 1 });
      return { retries: 100, counts: store.counts(), version: first.version };
    } finally {
      store.close();
    }
  });

  await runCheck("optimistic_version", async () => {
    const store = new LocalBusinessStore(primaryPath);
    try {
      const inserted = store.commitMutation(mutation("intent-version-insert", "insert", "record-version", "v1"));
      const updated = store.commitMutation(mutation("intent-version-update", "update", "record-version", "v2", 1));
      assert.equal(inserted.version, 1);
      assert.equal(updated.version, 2);
      assertStoreError(
        () => store.commitMutation(mutation("intent-version-stale", "update", "record-version", "stale", 1)),
        "VERSION_CONFLICT",
      );
      assert.equal(store.readRecord("record-version")?.value, "v2");
      return { inserted_version: inserted.version, updated_version: updated.version };
    } finally {
      store.close();
    }
  });

  await runCheck("crash_recovery", async () => {
    const databasePath = join(gateRoot, "crash.db");
    const rollbackPoints = ["after_begin", "after_mutation", "after_receipt"];
    for (const point of rollbackPoints) {
      const request = mutation(`intent-crash-${point}`, "insert", `record-crash-${point}`, point);
      const worker = await runWorker({ database_path: databasePath, mutation: request, exit_at: point });
      assert.equal(worker.code, 92, `${point}: ${worker.stderr || worker.stdout}`);
      const store = new LocalBusinessStore(databasePath);
      try {
        assert.equal(store.readRecord(request.record_id), undefined);
        assert.equal(store.readReceipt(request.intent_id), undefined);
        assert.equal(store.configuration().integrity_check, "ok");
      } finally {
        store.close();
      }
    }
    const committedRequest = mutation("intent-crash-after-commit", "insert", "record-crash-after-commit", "committed");
    const committedWorker = await runWorker({
      database_path: databasePath,
      mutation: committedRequest,
      exit_at: "after_commit",
    });
    assert.equal(committedWorker.code, 93, committedWorker.stderr || committedWorker.stdout);
    const store = new LocalBusinessStore(databasePath);
    try {
      const recovered = store.commitMutation(committedRequest);
      assert.equal(recovered.version, 1);
      assert.ok(store.readReceipt(committedRequest.intent_id));
      assert.deepEqual(store.counts(), { records: 1, receipts: 1 });
    } finally {
      store.close();
    }
    return { rolled_back_points: rollbackPoints, recovered_after_commit: true };
  });

  await runCheck("concurrent_version_conflict", async () => {
    const databasePath = join(gateRoot, "concurrent.db");
    const seed = new LocalBusinessStore(databasePath);
    seed.commitMutation(mutation("intent-concurrent-seed", "insert", "record-concurrent", "seed"));
    seed.close();
    const [left, right] = await Promise.all([
      runWorker({
        database_path: databasePath,
        mutation: mutation("intent-concurrent-left", "update", "record-concurrent", "left", 1),
      }),
      runWorker({
        database_path: databasePath,
        mutation: mutation("intent-concurrent-right", "update", "record-concurrent", "right", 1),
      }),
    ]);
    const outcomes = [left, right];
    assert.equal(outcomes.filter((outcome) => outcome.code === 0).length, 1);
    assert.equal(
      outcomes.filter((outcome) => outcome.code === 2 && outcome.json?.code === "VERSION_CONFLICT").length,
      1,
    );
    const store = new LocalBusinessStore(databasePath);
    try {
      assert.equal(store.readRecord("record-concurrent")?.version, 2);
      assert.deepEqual(store.counts(), { records: 1, receipts: 2 });
    } finally {
      store.close();
    }
    return { successful_writers: 1, version_conflicts: 1 };
  });

  await runCheck("concurrent_intent_idempotency", async () => {
    const databasePath = join(gateRoot, "same-intent.db");
    const initialized = new LocalBusinessStore(databasePath);
    initialized.close();
    const identical = mutation("intent-concurrent-same", "insert", "record-concurrent-same", "same value");
    const [first, second] = await Promise.all([
      runWorker({ database_path: databasePath, mutation: identical }),
      runWorker({ database_path: databasePath, mutation: identical }),
    ]);
    assert.equal(first.code, 0, first.stderr || first.stdout);
    assert.equal(second.code, 0, second.stderr || second.stdout);
    assert.deepEqual(first.json?.result, second.json?.result);
    const store = new LocalBusinessStore(databasePath);
    try {
      assert.deepEqual(store.counts(), { records: 1, receipts: 1 });
    } finally {
      store.close();
    }

    const conflictPath = join(gateRoot, "same-intent-conflict.db");
    const initializedConflict = new LocalBusinessStore(conflictPath);
    initializedConflict.close();
    const leftMutation = mutation("intent-concurrent-conflict", "insert", "record-conflict-left", "left");
    const rightMutation = mutation("intent-concurrent-conflict", "insert", "record-conflict-right", "right");
    const outcomes = await Promise.all([
      runWorker({ database_path: conflictPath, mutation: leftMutation }),
      runWorker({ database_path: conflictPath, mutation: rightMutation }),
    ]);
    const outcomeSummary = outcomes.map((outcome) => ({ code: outcome.code, json: outcome.json, stderr: outcome.stderr }));
    assert.equal(
      outcomes.filter((outcome) => outcome.code === 0).length,
      1,
      JSON.stringify(outcomeSummary),
    );
    assert.equal(
      outcomes.filter((outcome) => outcome.code === 2 && outcome.json?.code === "INTENT_PAYLOAD_CONFLICT").length,
      1,
      JSON.stringify(outcomeSummary),
    );
    const conflictStore = new LocalBusinessStore(conflictPath);
    try {
      assert.deepEqual(conflictStore.counts(), { records: 1, receipts: 1 });
    } finally {
      conflictStore.close();
    }
    return { identical_callers: 2, committed_mutations: 1, conflicting_payloads_rejected: 1 };
  });

  await runCheck("bounded_lock_wait", async () => {
    const databasePath = join(gateRoot, "locked.db");
    const seed = new LocalBusinessStore(databasePath);
    seed.commitMutation(mutation("intent-lock-seed", "insert", "record-lock", "seed"));
    seed.close();
    const lock = new DatabaseSync(databasePath, {
      allowExtension: false,
      defensive: true,
      enableDoubleQuotedStringLiterals: false,
      enableForeignKeyConstraints: true,
      timeout: 0,
    });
    lock.exec("BEGIN IMMEDIATE");
    let worker;
    try {
      worker = await runWorker({
        database_path: databasePath,
        timeout_ms: 250,
        mutation: mutation("intent-lock-waiter", "update", "record-lock", "waiter", 1),
      });
    } finally {
      lock.exec("ROLLBACK");
      lock.close();
    }
    assert.equal(worker.code, 2, worker.stderr || worker.stdout);
    assert.match(`${worker.json?.message ?? ""} ${worker.json?.code ?? ""}`, /busy|locked|SQLITE/i);
    assert.ok(worker.elapsed_ms >= 200, `lock wait was shorter than configured: ${worker.elapsed_ms} ms`);
    assert.ok(worker.elapsed_ms < 5_000, `lock wait was not bounded: ${worker.elapsed_ms} ms`);
    const verify = new LocalBusinessStore(databasePath);
    try {
      assert.equal(verify.readRecord("record-lock")?.value, "seed");
      assert.equal(verify.readReceipt("intent-lock-waiter"), undefined);
    } finally {
      verify.close();
    }
    return { configured_timeout_ms: 250, observed_elapsed_ms: worker.elapsed_ms };
  });

  await runCheck("backup_and_python_read", async () => {
    const backupPath = join(gateRoot, "backups", "primary-backup.db");
    const store = new LocalBusinessStore(primaryPath);
    let pages;
    try {
      pages = await store.backupTo(backupPath);
      await assert.rejects(
        () => store.backupTo(backupPath),
        (error) => error instanceof LocalBusinessStoreError && error.code === "TARGET_EXISTS",
      );
    } finally {
      store.close();
    }
    const readonly = new LocalBusinessStore(backupPath, { read_only: true });
    const counts = readonly.counts();
    try {
      assert.equal(readonly.configuration().integrity_check, "ok");
      assertStoreError(
        () => readonly.commitMutation(mutation("intent-readonly", "insert", "record-readonly", "blocked")),
        "READ_ONLY",
      );
    } finally {
      readonly.close();
    }
    assert.deepEqual(counts, { records: 2, receipts: 3 });
    const python = pythonCommand();
    const verification = spawnSync(
      python[0],
      [
        ...python.slice(1),
        "-m", "agent_platform.sqlite_gate",
        "--database", backupPath,
        "--expect-records", String(counts.records),
        "--expect-receipts", String(counts.receipts),
      ],
      { cwd: projectRoot, encoding: "utf8", windowsHide: true },
    );
    assert.equal(verification.status, 0, verification.stderr || verification.stdout);
    const pythonResult = JSON.parse(verification.stdout.trim());
    assert.equal(pythonResult.status, "ok");
    assert.equal(pythonResult.schema_version, 1);
    return { backup_pages: pages, counts, python: pythonResult };
  });

  finalStatus = "ok";
} catch (error) {
  failure = error instanceof Error ? { name: error.name, message: error.message } : { message: String(error) };
} finally {
  const report = {
    schema_version: 1,
    gate: "STORE-A0",
    status: finalStatus,
    started_at: startedAt,
    finished_at: new Date().toISOString(),
    environment: {
      node: process.versions.node,
      platform: process.platform,
      architecture: normalizedArchitecture(process.arch),
    },
    checks,
    ...(failure ? { failure } : {}),
  };
  writeReport(options.report, report);
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  if (!options.keep) rmSync(gateRoot, { recursive: true, force: true });
}

if (finalStatus !== "ok") process.exitCode = 1;

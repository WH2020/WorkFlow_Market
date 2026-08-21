import { LocalBusinessStore, LocalBusinessStoreError } from "../extensions/local-business-store.ts";

const encoded = process.env.AGENT4MARKET_SQLITE_GATE_REQUEST;
if (!encoded) {
  process.stderr.write("AGENT4MARKET_SQLITE_GATE_REQUEST is required\n");
  process.exit(64);
}

let request;
try {
  request = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
} catch (error) {
  process.stderr.write(`Invalid gate request: ${error instanceof Error ? error.message : String(error)}\n`);
  process.exit(64);
}

const exitAt = typeof request.exit_at === "string" ? request.exit_at : undefined;
const exitCode = exitAt === "after_commit" ? 93 : 92;
let store;
try {
  store = new LocalBusinessStore(request.database_path, { timeout_ms: request.timeout_ms ?? 5_000 });
  const result = store.commitMutation(request.mutation, (point) => {
    if (point === exitAt) process.exit(exitCode);
  });
  process.stdout.write(`${JSON.stringify({ status: "ok", result })}\n`);
  store.close();
} catch (error) {
  try {
    store?.close();
  } catch {
    // Preserve the original failure.
  }
  const code = error instanceof LocalBusinessStoreError
    ? error.code
    : typeof error === "object" && error && "code" in error
      ? String(error.code)
      : "UNEXPECTED";
  const message = error instanceof Error ? error.message : String(error);
  process.stdout.write(`${JSON.stringify({ status: "error", code, message })}\n`);
  process.exit(2);
}

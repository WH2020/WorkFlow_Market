import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { loadGovernedSubagentContract, type GovernedSubagentContract } from "./subagent-contracts.ts";
import { managedModelCatalog, runtimeModelRecipient, sameRecipient, taskScopedMessages } from "./model-selection.ts";
import registerCliProviders from "./cli-model-provider.ts";

/** The child process must reject a fallback before private context reaches any transport. */
export default function registerModelGuard(pi: ExtensionAPI): void {
  registerCliProviders(pi);
  let contract: GovernedSubagentContract | undefined;
  let failure: string | undefined;
  const abort = (ctx: ExtensionContext, error: unknown) => {
    failure = error instanceof Error ? error.message : "Subagent 模型边界校验失败";
    try { ctx.abort(); } catch { /* Always redact even if the abort hook fails. */ }
  };
  const check = (ctx: ExtensionContext) => {
    if (failure) throw new Error(failure);
    if (!contract) throw new Error("Subagent 缺少受管合同");
    if (contract.expected_model && contract.expected_model !== `${ctx.model?.provider}/${ctx.model?.id}`) {
      throw new Error("Subagent 实际模型与冻结合同不一致");
    }
    if ((contract.model_recipient || managedModelCatalog()) &&
        !sameRecipient(contract.model_recipient, runtimeModelRecipient(ctx.model))) {
      throw new Error("Subagent 实际接收方与冻结合同不一致");
    }
    if ((ctx.model?.api === "codex-cli" || ctx.model?.api === "claude-code" || contract.expected_thinking_level !== undefined) &&
        (contract.expected_thinking_level === undefined || pi.getThinkingLevel() !== contract.expected_thinking_level)) {
      throw new Error("Subagent 实际思考强度与冻结合同不一致");
    }
  };
  pi.on("before_agent_start", (event, ctx) => {
    try {
      // The first contract line is created by the parent, before the user-supplied text.
      const prefix = event.prompt.match(/\[DIRECTOR_TASK_CONTEXT ([A-Za-z0-9_-]+)\]\n受管任务：\1\n受管节点：([A-Za-z0-9_-]+)\ncontract_id：([a-f0-9-]{36})(?:\n|$)/u);
      if (!prefix) throw new Error("Subagent 缺少受管合同前缀");
      contract = loadGovernedSubagentContract(ctx.cwd, prefix[3]!);
      if (contract.task_id !== prefix[1] || contract.node_id !== prefix[2]) throw new Error("Subagent 合同与任务前缀不一致");
      failure = undefined;
      check(ctx);
    } catch (error) { abort(ctx, error); }
  });
  pi.on("context", (event, ctx) => {
    try {
      check(ctx);
      return { messages: contract!.model_recipient ? taskScopedMessages(event.messages, contract!.task_id, {
        entries: ctx.sessionManager.buildContextEntries(), model: contract!.expected_model, recipient: contract!.model_recipient,
      }) : event.messages };
    } catch (error) { abort(ctx, error); return { messages: [] }; }
  });
  pi.on("before_provider_request", (_event, ctx) => {
    try { check(ctx); } catch (error) { abort(ctx, error); return {}; }
  });
  // Readonly roles are short bounded runs; never summarize a fork's unfiltered history remotely.
  pi.on("session_before_compact", (_event, ctx) => {
    abort(ctx, new Error("只读子任务上下文已达上限，请缩小节点范围后重试"));
    return { cancel: true };
  });
  pi.on("session_before_tree", () => ({ cancel: true }));
}

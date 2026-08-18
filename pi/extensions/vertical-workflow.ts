import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  existsSync,
  lstatSync,
  readFileSync,
  realpathSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";
import { registerDataAdapters } from "./data-adapters.ts";
import {
  TaskStore,
  approveNode,
  acquireTaskLock,
  assertApprovedWriteIntent,
  beginWriteCommit,
  cancelTask,
  completeLogicalTool,
  completeModelNode,
  consumeApprovalRequest,
  createTask,
  currentNodes,
  isTerminal,
  proposeWriteIntent,
  recoverWriteFailure,
  releaseTaskLock,
  rejectApproval,
  type RuntimeWorkflow,
  type WorkflowTask,
} from "./task-runtime.ts";

type Service = {
  id: string;
  display_name: string;
  description: string;
  workflow: string;
  skill: string;
};

type Profile = {
  id: string;
  display_name: string;
  description: string;
  plugins: string[];
  default_service: string;
  services: Service[];
};

type WorkflowNode = {
  id: string;
  type: string;
  depends_on: string[];
  permissions: string[];
  skill?: string;
  tool?: string;
  policy?: string;
  check?: string;
  boundary?: {
    objective: string;
    allowed_tools: string[];
    max_turns: number;
    write_scope: string[];
  };
};

type Workflow = {
  id: string;
  plugin: string;
  display_name: string;
  entry_nodes: string[];
  output_nodes: string[];
  nodes: WorkflowNode[];
};

type WorkflowStage = { index: number; nodes: WorkflowNode[] };

type PluginManifest = {
  api_version: string;
  id: string;
  version: string;
  permissions: string[];
  dependencies: Array<{ id: string; version: string }>;
  skills: string[];
  workflows: string[];
};

type PluginBundle = {
  plugins: Map<string, PluginManifest>;
  workflows: Map<string, Workflow>;
};

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const profilesRoot = join(packageRoot, "profiles");
const NODE_TYPES = new Set(["agent", "tool", "subagent", "approval", "parallel", "join", "validator"]);
const LOGICAL_TOOL_PERMISSIONS = new Map([
  ["knowledge.search", "knowledge.read"],
  ["knowledge.write", "knowledge.write"],
  ["sales.read", "sales.read"],
  ["sales.write", "sales.write"],
  ["web.search", "web.read"],
]);
const SUBAGENT_TOOL_PERMISSIONS = new Map([
  ["web.search", "web.read"],
  ["web.open", "web.read"],
]);

function loadProfiles(): Map<string, Profile> {
  const profiles = new Map<string, Profile>();
  for (const entry of readdirSync(profilesRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const path = join(profilesRoot, entry.name, "profile.json");
    try {
      const profile = JSON.parse(readFileSync(path, "utf8")) as Profile;
      if (!profile.id || !Array.isArray(profile.services)) continue;
      const serialized = JSON.stringify(profile).toLowerCase();
      if (serialized.includes("wechat") || serialized.includes("weflow") || serialized.includes("微信")) {
        throw new Error(`Profile ${profile.id} contains a disabled chat integration`);
      }
      profiles.set(profile.id, profile);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") continue;
      throw error;
    }
  }
  if (profiles.size === 0) throw new Error(`No profiles found under ${profilesRoot}`);
  return profiles;
}

function findPluginManifests(root: string): string[] {
  const manifests: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) manifests.push(...findPluginManifests(path));
    if (entry.isFile() && entry.name === "plugin.json") manifests.push(path);
  }
  return manifests.sort();
}

function semanticVersion(value: string): [number, number, number] {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(value);
  if (!match) throw new Error(`Unsupported semantic version ${value}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function compareVersion(left: [number, number, number], right: [number, number, number]): number {
  for (let index = 0; index < 3; index += 1) {
    if (left[index] !== right[index]) return left[index]! - right[index]!;
  }
  return 0;
}

function satisfiesVersion(version: string, requirement: string): boolean {
  const actual = semanticVersion(version);
  const configured = requirement.replace(/^(>=|==|\^)/, "");
  const wanted = semanticVersion(configured);
  if (requirement.startsWith(">=")) return compareVersion(actual, wanted) >= 0;
  if (requirement.startsWith("==")) return compareVersion(actual, wanted) === 0;
  if (requirement.startsWith("^")) {
    const upper: [number, number, number] =
      wanted[0] > 0
        ? [wanted[0] + 1, 0, 0]
        : wanted[1] > 0
          ? [0, wanted[1] + 1, 0]
          : [0, 0, wanted[2] + 1];
    return compareVersion(actual, wanted) >= 0 && compareVersion(actual, upper) < 0;
  }
  return compareVersion(actual, wanted) === 0;
}

export function validateRuntimeWorkflow(workflow: Workflow, manifest: PluginManifest): void {
  if (!workflow.id || workflow.plugin !== manifest.id || !Array.isArray(workflow.nodes) || workflow.nodes.length === 0) {
    throw new Error(`Invalid workflow for plugin ${manifest.id}`);
  }
  const nodeMap = new Map<string, WorkflowNode>();
  const declaredPermissions = new Set(manifest.permissions);
  const declaredSkills = new Set(manifest.skills);
  for (const node of workflow.nodes) {
    if (!node.id || nodeMap.has(node.id)) throw new Error(`Workflow ${workflow.id} has an invalid/duplicate node ID`);
    if (!NODE_TYPES.has(node.type)) throw new Error(`Workflow ${workflow.id}/${node.id} has unknown node type`);
    if (!Array.isArray(node.depends_on) || !Array.isArray(node.permissions)) {
      throw new Error(`Workflow ${workflow.id}/${node.id} is missing dependency/permission arrays`);
    }
    if (node.depends_on.length !== new Set(node.depends_on).size) {
      throw new Error(`Workflow ${workflow.id}/${node.id} has duplicate dependencies`);
    }
    const excess = node.permissions.filter((permission) => !declaredPermissions.has(permission));
    if (excess.length > 0) throw new Error(`Workflow ${workflow.id}/${node.id} escalates permissions: ${excess.join(", ")}`);
    const structuredWrites = node.permissions.filter(
      (permission) => permission.endsWith(".write") && permission !== "artifact.write",
    );
    if (structuredWrites.length > 0 && node.type !== "tool") {
      throw new Error(`Workflow ${workflow.id}/${node.id} uses structured write outside a tool node`);
    }
    if (node.type === "agent" && (!node.skill || !declaredSkills.has(node.skill))) {
      throw new Error(`Workflow ${workflow.id}/${node.id} uses an undeclared skill`);
    }
    if (node.type === "tool") {
      const required = node.tool ? LOGICAL_TOOL_PERMISSIONS.get(node.tool) : undefined;
      if (!required || !node.permissions.includes(required)) {
        throw new Error(`Workflow ${workflow.id}/${node.id} has an unknown or unauthorized logical tool`);
      }
    }
    if (node.type === "approval" && !node.policy) throw new Error(`Workflow ${workflow.id}/${node.id} lacks approval policy`);
    if (node.type === "validator" && !node.check) throw new Error(`Workflow ${workflow.id}/${node.id} lacks validator check`);
    if (node.type === "subagent") {
      const boundary = node.boundary;
      if (!boundary || !boundary.objective.trim() || !Array.isArray(boundary.allowed_tools)) {
        throw new Error(`Workflow ${workflow.id}/${node.id} has no subagent boundary`);
      }
      if (!Number.isInteger(boundary.max_turns) || boundary.max_turns < 1 || boundary.max_turns > 20) {
        throw new Error(`Workflow ${workflow.id}/${node.id} has invalid subagent max_turns`);
      }
      if (!Array.isArray(boundary.write_scope) || boundary.write_scope.length !== 0) {
        throw new Error(`Workflow ${workflow.id}/${node.id} subagent must be read-only`);
      }
      for (const tool of boundary.allowed_tools) {
        const required = SUBAGENT_TOOL_PERMISSIONS.get(tool);
        if (!required || !node.permissions.includes(required)) {
          throw new Error(`Workflow ${workflow.id}/${node.id} has an unauthorized subagent tool`);
        }
      }
    }
    nodeMap.set(node.id, node);
  }
  for (const node of nodeMap.values()) {
    if (node.depends_on.some((dependency) => !nodeMap.has(dependency) || dependency === node.id)) {
      throw new Error(`Workflow ${workflow.id}/${node.id} has an invalid dependency`);
    }
    if (node.type === "join" && node.depends_on.length < 2) {
      throw new Error(`Workflow ${workflow.id}/${node.id} join requires two dependencies`);
    }
  }
  planWorkflow(workflow);
  const actualEntries = [...nodeMap.values()].filter((node) => node.depends_on.length === 0).map((node) => node.id).sort();
  const successors = new Map([...nodeMap.keys()].map((id) => [id, 0]));
  for (const node of nodeMap.values()) {
    for (const dependency of node.depends_on) successors.set(dependency, successors.get(dependency)! + 1);
  }
  const actualOutputs = [...successors].filter(([, count]) => count === 0).map(([id]) => id).sort();
  if (JSON.stringify([...workflow.entry_nodes].sort()) !== JSON.stringify(actualEntries)) {
    throw new Error(`Workflow ${workflow.id} entry_nodes do not match the DAG`);
  }
  if (JSON.stringify([...workflow.output_nodes].sort()) !== JSON.stringify(actualOutputs)) {
    throw new Error(`Workflow ${workflow.id} output_nodes do not match the DAG`);
  }
  for (const node of nodeMap.values()) {
    if (node.type !== "tool" || (node.tool !== "knowledge.write" && node.tool !== "sales.write")) continue;
    const directApprovals = node.depends_on.filter((dependency) => nodeMap.get(dependency)?.type === "approval");
    if (directApprovals.length !== 1 || node.depends_on.length !== 1) {
      throw new Error(`Workflow ${workflow.id}/${node.id} write must have exactly one direct approval dependency`);
    }
    const approvalId = directApprovals[0]!;
    const approvalNode = nodeMap.get(approvalId)!;
    if (
      approvalNode.depends_on.length !== 1 ||
      !["agent", "validator"].includes(nodeMap.get(approvalNode.depends_on[0]!)?.type ?? "")
    ) {
      throw new Error(`Workflow ${workflow.id}/${approvalId} write approval must have one direct agent/validator predecessor`);
    }
    const protectedWrites = workflow.nodes.filter(
      (candidate) =>
        candidate.type === "tool" &&
        (candidate.tool === "knowledge.write" || candidate.tool === "sales.write") &&
        candidate.depends_on.includes(approvalId),
    );
    if (protectedWrites.length !== 1) {
      throw new Error(`Workflow ${workflow.id}/${approvalId} approval must protect exactly one direct structured write`);
    }
  }
}

function loadPluginBundle(): PluginBundle {
  const plugins = new Map<string, PluginManifest>();
  const workflows = new Map<string, Workflow>();
  for (const manifestPath of findPluginManifests(join(packageRoot, "vertical_plugins"))) {
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as PluginManifest;
    if (
      !manifest.id ||
      manifest.api_version !== "1.0" ||
      !manifest.version ||
      !Array.isArray(manifest.permissions) ||
      !Array.isArray(manifest.dependencies) ||
      !Array.isArray(manifest.skills) ||
      !Array.isArray(manifest.workflows)
    ) {
      throw new Error(`Invalid plugin manifest ${manifestPath}`);
    }
    semanticVersion(manifest.version);
    const serializedManifest = JSON.stringify(manifest).toLowerCase();
    if (serializedManifest.includes("wechat") || serializedManifest.includes("weflow") || serializedManifest.includes("微信")) {
      throw new Error(`Plugin ${manifest.id} contains a disabled chat integration`);
    }
    if (plugins.has(manifest.id)) throw new Error(`Duplicate plugin ${manifest.id}`);
    plugins.set(manifest.id, manifest);
    for (const relativePath of manifest.workflows) {
      const pluginRoot = dirname(manifestPath);
      const workflowPath = resolve(pluginRoot, relativePath);
      const containment = relative(pluginRoot, workflowPath);
      if (containment.startsWith("..") || isAbsolute(containment)) {
        throw new Error(`Workflow path escapes plugin directory: ${relativePath}`);
      }
      const workflow = JSON.parse(
        readFileSync(workflowPath, "utf8"),
      ) as Workflow;
      if (workflows.has(workflow.id)) throw new Error(`Duplicate workflow ${workflow.id}`);
      const serializedWorkflow = JSON.stringify(workflow).toLowerCase();
      if (serializedWorkflow.includes("wechat") || serializedWorkflow.includes("weflow") || serializedWorkflow.includes("微信")) {
        throw new Error(`Workflow ${workflow.id} contains a disabled chat integration`);
      }
      validateRuntimeWorkflow(workflow, manifest);
      workflows.set(workflow.id, workflow);
    }
  }
  return { plugins, workflows };
}

function resolveProfilePlugins(profile: Profile, plugins: Map<string, PluginManifest>): string[] {
  const ordered: string[] = [];
  const state = new Map<string, "visiting" | "done">();
  const visit = (pluginId: string) => {
    if (state.get(pluginId) === "visiting") throw new Error(`Plugin dependency cycle at ${pluginId}`);
    if (state.get(pluginId) === "done") return;
    const plugin = plugins.get(pluginId);
    if (!plugin) throw new Error(`Profile ${profile.id} requires missing plugin ${pluginId}`);
    state.set(pluginId, "visiting");
    for (const dependency of plugin.dependencies) {
      visit(dependency.id);
      const installed = plugins.get(dependency.id)!;
      if (!satisfiesVersion(installed.version, dependency.version)) {
        throw new Error(`${plugin.id} requires ${dependency.id} ${dependency.version}, found ${installed.version}`);
      }
    }
    state.set(pluginId, "done");
    ordered.push(pluginId);
  };
  for (const pluginId of profile.plugins) visit(pluginId);
  return ordered;
}

function loadSkillCatalog(): Map<string, string> {
  const source = JSON.parse(
    readFileSync(join(packageRoot, "pi", "skill-catalog.json"), "utf8"),
  ) as Record<string, string>;
  return new Map(Object.entries(source));
}

function profileSkillPaths(
  profile: Profile,
  plugins: Map<string, PluginManifest>,
  catalog: Map<string, string>,
): string[] {
  const skillNames = new Set<string>();
  for (const pluginId of resolveProfilePlugins(profile, plugins)) {
    for (const skill of plugins.get(pluginId)!.skills) skillNames.add(skill);
  }
  return [...skillNames].sort().map((skill) => {
    const configuredPath = catalog.get(skill);
    if (!configuredPath) throw new Error(`Skill ${skill} is missing from pi/skill-catalog.json`);
    const absolutePath = resolve(packageRoot, configuredPath);
    const containment = relative(packageRoot, absolutePath);
    if (containment.startsWith("..") || isAbsolute(containment)) {
      throw new Error(`Skill path escapes package root: ${configuredPath}`);
    }
    readFileSync(join(absolutePath, "SKILL.md"), "utf8");
    return absolutePath;
  });
}

function validateProfileBindings(
  profile: Profile,
  plugins: Map<string, PluginManifest>,
  workflows: Map<string, Workflow>,
  catalog: Map<string, string>,
) {
  const resolvedPlugins = new Set(resolveProfilePlugins(profile, plugins));
  const resolvedSkills = new Set(
    [...resolvedPlugins].flatMap((pluginId) => plugins.get(pluginId)!.skills),
  );
  const serviceIds = new Set<string>();
  for (const service of profile.services) {
    if (serviceIds.has(service.id)) throw new Error(`Duplicate service ${profile.id}/${service.id}`);
    serviceIds.add(service.id);
    const workflow = workflows.get(service.workflow);
    if (!workflow || !resolvedPlugins.has(workflow.plugin)) {
      throw new Error(`Service ${profile.id}/${service.id} references unavailable workflow`);
    }
    if (!resolvedSkills.has(service.skill) || !catalog.has(service.skill)) {
      throw new Error(`Service ${profile.id}/${service.id} references unavailable skill`);
    }
  }
  if (!serviceIds.has(profile.default_service)) {
    throw new Error(`Profile ${profile.id} has an invalid default service`);
  }
  profileSkillPaths(profile, plugins, catalog);
}

function planWorkflow(workflow: Workflow): WorkflowStage[] {
  const nodes = new Map(workflow.nodes.map((node) => [node.id, node]));
  const remaining = new Set(nodes.keys());
  const completed = new Set<string>();
  const stages: WorkflowStage[] = [];
  while (remaining.size > 0) {
    const ready = [...remaining]
      .filter((id) => (nodes.get(id)?.depends_on ?? []).every((dependency) => completed.has(dependency)))
      .sort();
    if (ready.length === 0) throw new Error(`Workflow ${workflow.id} is cyclic or has missing dependencies`);
    stages.push({ index: stages.length, nodes: ready.map((id) => nodes.get(id)!) });
    for (const id of ready) {
      remaining.delete(id);
      completed.add(id);
    }
  }
  return stages;
}

function renderPlan(workflow: Workflow): string {
  const stages = planWorkflow(workflow).map((stage) => ({
    stage: stage.index,
    nodes: stage.nodes.map((node) => ({
      id: node.id,
      type: node.type,
      depends_on: node.depends_on,
      skill: node.skill,
      tool: node.tool,
      policy: node.policy,
      check: node.check,
      boundary: node.boundary,
      execution_mode:
        node.type === "approval"
          ? "human_gate"
          : node.type === "subagent"
            ? "bounded_subagent"
            : "automatic",
    })),
  }));
  return JSON.stringify(
    {
      workflow: workflow.id,
      plugin: workflow.plugin,
      entry_nodes: workflow.entry_nodes,
      output_nodes: workflow.output_nodes,
      stages,
    },
    null,
    2,
  );
}

function profileContext(profile: Profile): string {
  const services = profile.services
    .map((service) => `- ${service.id}: ${service.display_name}；工作流 ${service.workflow}；Skill $${service.skill}`)
    .join("\n");
  return [
    `当前垂直角色：${profile.display_name}（${profile.id}）。`,
    profile.description,
    "你是主 Agent：先识别用户意图并选择服务，随后必须调用 get_vertical_workflow_plan 取得对应 DAG，再按阶段推进。只有可独立研究、只读检查或独立复核才使用 subagent；审批、数据写入和正式文件生成必须保留为确定性节点或人工关口。",
    "到达 approval 节点时必须停止并请求用户确认；未确认前不得执行后续阶段。subagent 必须遵守计划中的 objective、allowed_tools、max_turns 和 write_scope；当前运行时没有隔离 subagent 执行器时必须停在该节点并如实报告，不能把它当作普通 agent 或静默完成。",
    "启动任务时使用逐项核对：一次只提出一个会显著改变方向、范围、接口、风险或交付物的问题；记录已确认、暂定和待确认项，发现矛盾时直接指出并继续核对。信息已足够时不要机械追问。",
    "DAG 中的 tool 字段是逻辑能力 ID。使用当前已安装的 Pi 工具和对应 Skill 实现；若没有可用适配器，停在该节点并明确报告，不得声称已调用不存在的工具。",
    "信息必须区分已证实事实、分析判断、待验证假设和未知信息。缺失信息只有会显著改变方向、接口、风险或交付物时才提问。",
    "当前版本不接入微信或 WeFlow，也不得自行恢复此类入口。",
    "可用服务：",
    services,
  ].join("\n");
}

function renderServices(profile: Profile): string {
  return profile.services
    .map((service) => `${service.id}｜${service.display_name}｜${service.description}`)
    .join("\n");
}

const ADAPTER_TO_LOGICAL_TOOL = new Map([
  ["director_knowledge_search", "knowledge.search"],
  ["director_knowledge_write", "knowledge.write"],
  ["director_sales_read", "sales.read"],
  ["director_sales_write", "sales.write"],
  ["director_web_search", "web.search"],
]);

const MANAGED_ALLOWED_TOOLS = new Set([
  "read",
  "grep",
  "find",
  "ls",
  "write",
  "edit",
  "bash",
  "get_vertical_workflow_plan",
  "director_complete_node",
  "director_propose_write_intent",
  ...ADAPTER_TO_LOGICAL_TOOL.keys(),
]);

type StoredEntry = { type?: string; customType?: string; data?: unknown };
export type WorkbenchRequest = {
  schema_version: "1.0";
  request_id: string;
  status: "requested" | "accepted";
  profile_id: string;
  service_id: string;
  workflow_id: string;
  request: string;
  created_at: string;
  source: "local-workbench";
  task_id?: string;
  accepted_at?: string;
};

export function selectWorkbenchRequest(
  requests: WorkbenchRequest[],
  activeProfileId: string,
): WorkbenchRequest | undefined {
  const pending = requests.filter((request) => request.status === "requested");
  return pending.find((request) => request.profile_id === activeProfileId) ?? pending[0];
}

function validateWorkbenchRequest(value: unknown, expectedRequestId?: string): WorkbenchRequest {
  if (!value || typeof value !== "object") throw new Error("工作台请求必须是对象");
  const request = value as Partial<WorkbenchRequest>;
  const safe = (candidate: unknown) => typeof candidate === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(candidate);
  const safeWorkflow = (candidate: unknown) => typeof candidate === "string" && /^[A-Za-z0-9_.-]{1,160}$/.test(candidate);
  if (
    request.schema_version !== "1.0" ||
    request.source !== "local-workbench" ||
    (request.status !== "requested" && request.status !== "accepted") ||
    !safe(request.request_id) ||
    !safe(request.profile_id) ||
    !safe(request.service_id) ||
    !safeWorkflow(request.workflow_id) ||
    typeof request.request !== "string" ||
    request.request.trim().length < 1 ||
    request.request.length > 4000 ||
    typeof request.created_at !== "string" ||
    (expectedRequestId !== undefined && request.request_id !== expectedRequestId)
  ) {
    throw new Error("工作台请求字段无效或与文件名不一致");
  }
  return request as WorkbenchRequest;
}

function isWorkflowTask(value: unknown): value is WorkflowTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<WorkflowTask>;
  return (
    task.schema_version === "1.0" &&
    typeof task.task_id === "string" &&
    typeof task.session_key === "string" &&
    typeof task.workflow_id === "string" &&
    typeof task.version === "number" &&
    Array.isArray(task.completed_nodes) &&
    Array.isArray(task.waiting_nodes) &&
    Array.isArray(task.artifacts) &&
    Array.isArray(task.audit)
  );
}

function renderRuntimeState(state: WorkflowTask, workflow: Workflow): string {
  const pendingNodes = currentNodes(state, workflow as RuntimeWorkflow);
  const pending = pendingNodes
    .map((node) => `${node.id}(${node.type}${node.tool ? `:${node.tool}` : ""})`)
    .join(", ");
  return [
    `任务：${state.task_id}`,
    `状态：${state.status}`,
    `角色/服务：${state.profile_id}/${state.service_id}`,
    `工作流：${state.workflow_id}`,
    `版本：${state.version}`,
    `当前阶段：${state.current_stage ?? "已结束"}`,
    `当前节点：${pending || "无"}`,
    `待审批：${state.waiting_nodes.join(", ") || "无"}`,
    ...(state.pending_write
      ? [
          `冻结写入：${state.pending_write.logical_tool} / ${state.pending_write.status}`,
          `写入意图：${state.pending_write.intent_id}`,
          `载荷哈希：${state.pending_write.payload_sha256}`,
          `具体变更：${state.pending_write.canonical_payload}`,
        ]
      : []),
    ...(pendingNodes.some((node) => node.type === "subagent")
      ? ["阻塞说明：当前版本尚未安装隔离 subagent 执行器；该节点不会被静默完成。"]
      : []),
  ].join("\n");
}

function sameExternalDecisionBase(memory: WorkflowTask, disk: WorkflowTask): boolean {
  const select = (task: WorkflowTask) => ({
    schema_version: task.schema_version,
    task_id: task.task_id,
    session_key: task.session_key,
    profile_id: task.profile_id,
    service_id: task.service_id,
    workflow_id: task.workflow_id,
    request: task.request,
    status: task.status,
    completed_nodes: task.completed_nodes,
    current_stage: task.current_stage,
    current_node: task.current_node,
    waiting_nodes: task.waiting_nodes,
    waiting_node: task.waiting_node,
    artifacts: task.artifacts,
    pending_write: task.pending_write,
    created_at: task.created_at,
    audit: task.audit,
  });
  return JSON.stringify(select(memory)) === JSON.stringify(select(disk));
}

function inputPathCandidates(input: unknown): string[] {
  const candidates: string[] = [];
  const collect = (value: unknown, key = "") => {
    if (typeof value === "string" && /(^|_)(path|file|filename)$/i.test(key)) candidates.push(value);
    if (!value || typeof value !== "object") return;
    for (const [childKey, childValue] of Object.entries(value as Record<string, unknown>)) {
      collect(childValue, childKey);
    }
  };
  collect(input);
  return candidates;
}

function pathIsWithin(projectRoot: string, configuredRoot: string, candidate: string): boolean {
  const allowedRoot = resolve(projectRoot, configuredRoot);
  const target = resolve(projectRoot, candidate);
  const containment = relative(allowedRoot, target);
  return containment === "" || (!containment.startsWith("..") && !isAbsolute(containment));
}

function inputTargetsData(input: unknown, projectRoot: string): boolean {
  const candidates = inputPathCandidates(input);
  return candidates.some((candidate) => {
    return pathIsWithin(projectRoot, "data", candidate);
  });
}

function inputTargetsOnlyOutputs(input: unknown, projectRoot: string): boolean {
  const candidates = inputPathCandidates(input);
  const allowedRoot = resolve(projectRoot, "outputs");
  if (!existsSync(allowedRoot) || lstatSync(allowedRoot).isSymbolicLink()) return false;
  const canonicalRoot = realpathSync.native(allowedRoot);
  return candidates.length > 0 && candidates.every((candidate) => {
    const target = resolve(projectRoot, candidate);
    if (!pathIsWithin(projectRoot, "outputs", candidate)) return false;
    const relativeTarget = relative(allowedRoot, target);
    const parts = relativeTarget.split(sep).filter(Boolean);
    let cursor = allowedRoot;
    for (const part of parts) {
      cursor = join(cursor, part);
      if (!existsSync(cursor)) break;
      const metadata = lstatSync(cursor);
      if (metadata.isSymbolicLink()) return false;
      const canonical = realpathSync.native(cursor);
      const containment = relative(canonicalRoot, canonical);
      if (containment.startsWith("..") || isAbsolute(containment)) return false;
    }
    return true;
  });
}

const MANAGED_READ_TOOLS = new Set(["read", "grep", "find", "ls"]);
const SENSITIVE_READ_SEGMENTS = new Set([".git", ".pi", "node_modules", "data"]);

function inputTargetsSafeProjectRead(input: unknown, projectRoot: string): boolean {
  const candidates = inputPathCandidates(input);
  if (candidates.length === 0) return false;
  const canonicalRoot = realpathSync.native(resolve(projectRoot));
  return candidates.every((candidate) => {
    if (isAbsolute(candidate)) return false;
    const parts = candidate.split(/[\\/]+/u).filter(Boolean);
    if (parts.includes("..") || parts.some((part) => SENSITIVE_READ_SEGMENTS.has(part.toLowerCase()))) return false;
    const fileName = parts.at(-1)?.toLowerCase() ?? "";
    if (
      fileName === ".env" || fileName.startsWith(".env.") ||
      fileName.endsWith(".pem") || fileName.endsWith(".key") ||
      fileName === "id_rsa" || fileName === "id_ed25519" ||
      fileName.startsWith("credentials") || fileName.startsWith("secrets")
    ) return false;
    const target = resolve(projectRoot, candidate);
    if (!pathIsWithin(projectRoot, ".", candidate)) return false;
    let cursor = target;
    while (!existsSync(cursor)) {
      const parent = dirname(cursor);
      if (parent === cursor) return false;
      cursor = parent;
    }
    if (lstatSync(cursor).isSymbolicLink()) return false;
    const canonical = realpathSync.native(cursor);
    const containment = relative(canonicalRoot, canonical);
    return !containment.startsWith("..") && !isAbsolute(containment);
  });
}

export default function verticalWorkflow(pi: ExtensionAPI) {
  const profiles = loadProfiles();
  const { plugins, workflows } = loadPluginBundle();
  const skillCatalog = loadSkillCatalog();
  for (const profile of profiles.values()) {
    validateProfileBindings(profile, plugins, workflows, skillCatalog);
  }
  const requested = process.env.WORKFLOW_AGENT_PROFILE || "market-director";
  const initialProfile = profiles.get(requested);
  if (!initialProfile) {
    throw new Error(
      `Unknown WORKFLOW_AGENT_PROFILE ${requested}; available: ${[...profiles.keys()].join(", ")}`,
    );
  }
  let activeProfile: Profile = initialProfile;
  let projectRoot = process.cwd();
  let sessionKey = "";
  let activeTask: WorkflowTask | undefined;
  let taskStore = new TaskStore(projectRoot);
  let requestPoller: ReturnType<typeof setInterval> | undefined;
  let initialPoller: ReturnType<typeof setTimeout> | undefined;
  let requestPollBusy = false;
  let profileSwitchQueued = false;

  const workflowFor = (state: WorkflowTask): Workflow => {
    const workflow = workflows.get(state.workflow_id);
    if (!workflow) throw new Error(`Workflow ${state.workflow_id} is not installed`);
    return workflow;
  };

  const persistNew = (state: WorkflowTask) => {
    taskStore.save(state);
    activeTask = state;
    pi.appendEntry("director-task-state", state);
  };

  const persistTransition = (previous: WorkflowTask, next: WorkflowTask) => {
    taskStore.save(next, previous.version);
    activeTask = next;
    pi.appendEntry("director-task-state", next);
  };

  const sendTaskPrompt = (task: WorkflowTask, service: Service, workflow: Workflow) => {
    pi.sendUserMessage(
      `/skill:${service.skill} 当前角色：${activeProfile.display_name}。这是受管任务 ${task.task_id}。严格按以下 DAG 执行：agent/validator 节点完成后调用 director_complete_node；如果该节点下一步是保护知识库或销售台账写入的 approval，必须先用 director_propose_write_intent 冻结后续 director_*_write 的完整批次参数，再完成节点；逻辑 tool 节点只调用匹配的 director_* 适配器；approval 只能由用户命令推进。不得跳阶段。\n${renderPlan(workflow)}\n${renderRuntimeState(task, workflow)}\n用户任务：${task.request}`,
      { expandPromptTemplates: true },
    );
  };

  const consumeWorkbenchRequest = async (): Promise<WorkflowTask | undefined> => {
    if (requestPollBusy || profileSwitchQueued || (activeTask && !isTerminal(activeTask))) return;
    requestPollBusy = true;
    try {
      const requestRoot = resolve(projectRoot, ".pi", "director-runtime", "requests");
      if (!existsSync(requestRoot)) return;
      const candidates = readdirSync(requestRoot)
        .filter((name) => /^request-[A-Za-z0-9_-]+\.json$/.test(name))
        .sort()
        .slice(0, 500);
      const parsed = candidates.flatMap((name) => {
        const path = join(requestRoot, name);
        try {
          if (statSync(path).size > 16_384) return [];
          const expectedRequestId = basename(name, ".json");
          const request = validateWorkbenchRequest(JSON.parse(readFileSync(path, "utf8")), expectedRequestId);
          return [{ path, request }];
        } catch {
          return [];
        }
      });
      const selectedRequest = selectWorkbenchRequest(
        parsed.map((candidate) => candidate.request),
        activeProfile.id,
      );
      if (!selectedRequest) return;
      const selected = parsed.find((candidate) => candidate.request === selectedRequest)!;
      let request = selected.request;
      const targetProfile = profiles.get(request.profile_id);
      if (!targetProfile) return;
      const targetService = targetProfile.services.find((item) => item.id === request.service_id);
      if (
        !targetService ||
        targetService.workflow !== request.workflow_id ||
        !request.request.trim() ||
        !workflows.has(targetService.workflow)
      ) {
        return;
      }
      if (targetProfile.id !== activeProfile.id) {
        profileSwitchQueued = true;
        pi.sendUserMessage(`/director-apply-profile-switch ${targetProfile.id}`, {
          deliverAs: "followUp",
          expandPromptTemplates: true,
        });
        return;
      }
      const path = selected.path;
      const service = activeProfile.services.find((item) => item.id === request.service_id);
      if (!service) return;
      const workflow = workflows.get(service.workflow);
      if (!workflow) return;
      const lockPath = `${path}.lock`;
      let lock: number | undefined;
      let temporary: string | undefined;
      try {
        lock = acquireTaskLock(lockPath);
        if (statSync(path).size > 16_384) throw new Error("工作台请求超过 16 KiB 安全上限");
        request = validateWorkbenchRequest(JSON.parse(readFileSync(path, "utf8")), basename(path, ".json"));
        if (
          request.status !== "requested" ||
          request.profile_id !== activeProfile.id ||
          request.service_id !== selectedRequest.service_id ||
          request.workflow_id !== selectedRequest.workflow_id ||
          request.request !== selectedRequest.request ||
          request.request_id !== selectedRequest.request_id
        ) return;
        const existing = taskStore.load(request.request_id);
        if (
          existing &&
          (existing.session_key !== sessionKey ||
            existing.profile_id !== request.profile_id ||
            existing.service_id !== request.service_id ||
            existing.workflow_id !== request.workflow_id ||
            existing.request !== request.request.trim() ||
            isTerminal(existing))
        ) {
          throw new Error(`任务 ID ${request.request_id} 已属于另一个任务，拒绝接管`);
        }
        const task =
          existing ??
          createTask({
            sessionKey,
            profileId: activeProfile.id,
            serviceId: service.id,
            workflow: workflow as RuntimeWorkflow,
            request: request.request.trim(),
            taskId: request.request_id,
          });
        if (!existing) persistNew(task);
        else activeTask = existing;
        const accepted: WorkbenchRequest = {
          ...request,
          status: "accepted",
          task_id: task.task_id,
          accepted_at: new Date().toISOString(),
        };
        temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
        writeFileSync(temporary, `${JSON.stringify(accepted, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
        renameSync(temporary, path);
        temporary = undefined;
        if (!isTerminal(task)) sendTaskPrompt(task, service, workflow);
        return task;
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      } finally {
        if (lock !== undefined) releaseTaskLock(lockPath, lock);
        if (temporary && existsSync(temporary)) rmSync(temporary, { force: true });
      }
    } finally {
      requestPollBusy = false;
    }
  };

  const consumeExternalDecision = (): WorkflowTask | undefined => {
    if (!activeTask || isTerminal(activeTask)) return activeTask;
    const disk = taskStore.load(activeTask.task_id);
    const request = disk?.approval_request;
    if (!disk || !request) return activeTask;
    if (activeTask.version !== request.expected_version && activeTask.version !== disk.version) {
      throw new Error("外部审批请求基于过期任务版本；拒绝自动合并");
    }
    if (activeTask.version === request.expected_version && !sameExternalDecisionBase(activeTask, disk)) {
      throw new Error("外部审批请求同时修改了受保护任务字段；拒绝推进");
    }
    const workflow = workflowFor(disk);
    const next = consumeApprovalRequest(disk, workflow as RuntimeWorkflow);
    persistTransition(disk, next);
    return next;
  };

  const pollRuntime = async (): Promise<void> => {
    const previous = activeTask;
    const synchronized = consumeExternalDecision();
    if (
      previous &&
      synchronized &&
      synchronized.version !== previous.version &&
      synchronized.status === "running"
    ) {
      pi.sendUserMessage(
        `本地工作台已批准受管任务 ${synchronized.task_id} 的人工关口。继续且仅执行当前阶段节点。`,
        { deliverAs: "followUp" },
      );
    }
    await consumeWorkbenchRequest();
  };

  const requireLogicalTool = (logicalTool: string): { state: WorkflowTask; workflow: Workflow } => {
    consumeExternalDecision();
    if (!activeTask || isTerminal(activeTask)) throw new Error("当前会话没有运行中的受管任务");
    if (activeTask.status === "waiting_approval") throw new Error("任务正在等待用户审批");
    const workflow = workflowFor(activeTask);
    const matches = currentNodes(activeTask, workflow as RuntimeWorkflow).filter(
      (node) => node.type === "tool" && node.tool === logicalTool,
    );
    if (matches.length !== 1) {
      throw new Error(`逻辑工具 ${logicalTool} 与当前 DAG 节点不匹配`);
    }
    return { state: activeTask, workflow };
  };

  registerDataAdapters(pi, {
    projectRoot: () => projectRoot,
    beforeLogicalTool: (logicalTool, params) => {
      const { state, workflow } = requireLogicalTool(logicalTool);
      if (logicalTool === "knowledge.write" || logicalTool === "sales.write") {
        assertApprovedWriteIntent(state, logicalTool, params);
        if (state.pending_write?.status === "approved") {
          const next = beginWriteCommit(
            state,
            workflow as RuntimeWorkflow,
            logicalTool,
            params,
            state.version,
          );
          persistTransition(state, next);
        }
        const intent = activeTask?.pending_write;
        if (!intent || intent.status !== "committing") throw new Error("写入提交状态未能持久化");
        return { intent_id: intent.intent_id, payload_sha256: intent.payload_sha256 };
      }
    },
    afterLogicalTool: (logicalTool, params, details) => {
      const { state, workflow } = requireLogicalTool(logicalTool);
      const next = completeLogicalTool(
        state,
        workflow as RuntimeWorkflow,
        logicalTool,
        state.version,
        JSON.stringify(details).slice(0, 1000),
        params,
      );
      persistTransition(state, next);
    },
    onLogicalToolError: (logicalTool, params, outcome, error) => {
      if (!activeTask || isTerminal(activeTask)) return;
      const previous = activeTask;
      const next = recoverWriteFailure(
        previous,
        logicalTool,
        params,
        outcome,
        previous.version,
        (error as Error).message,
      );
      persistTransition(previous, next);
    },
  });

  pi.on("session_start", (_event, ctx) => {
    profileSwitchQueued = false;
    projectRoot = resolve(ctx.cwd);
    taskStore = new TaskStore(projectRoot);
    const entries = ctx.sessionManager.getEntries() as StoredEntry[];
    const storedSessionKey = [...entries]
      .reverse()
      .find((entry) => entry.type === "custom" && entry.customType === "director-session-key")?.data;
    const storedTasks = entries
      .filter((entry) => entry.type === "custom" && entry.customType === "director-task-state")
      .map((entry) => entry.data)
      .filter(isWorkflowTask);
    const lastStoredTask = storedTasks.at(-1);
    sessionKey =
      ctx.sessionManager.getSessionFile() ??
      (typeof storedSessionKey === "string" ? storedSessionKey : lastStoredTask?.session_key) ??
      `unsaved-${randomUUID()}`;
    if (typeof storedSessionKey !== "string") pi.appendEntry("director-session-key", sessionKey);

    activeTask = undefined;
    if (lastStoredTask) {
      const disk = taskStore.load(lastStoredTask.task_id);
      activeTask =
        disk?.approval_request && disk.version === lastStoredTask.version + 1
          ? lastStoredTask
          : disk && disk.version >= lastStoredTask.version
            ? disk
            : lastStoredTask;
    } else {
      activeTask = taskStore.findActive(sessionKey);
    }
    if (activeTask && !isTerminal(activeTask)) {
      const recoveredProfile = profiles.get(activeTask.profile_id);
      if (!recoveredProfile) throw new Error(`Recovered task references unknown Profile ${activeTask.profile_id}`);
      activeProfile = recoveredProfile;
      process.env.WORKFLOW_AGENT_PROFILE = recoveredProfile.id;
    }
    const taskStatus = activeTask && !isTerminal(activeTask) ? `｜任务：${activeTask.status}` : "";
    ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}${taskStatus}`);
    if (requestPoller) clearInterval(requestPoller);
    requestPoller = setInterval(() => {
      void pollRuntime().catch(() => {
        // A command or tool boundary will surface persistent runtime errors to the user.
      });
    }, 2000);
    if (initialPoller) clearTimeout(initialPoller);
    initialPoller = setTimeout(() => {
      void pollRuntime().catch(() => {
        // The next explicit command/tool boundary reports persistent runtime errors.
      });
    }, 0);
  });

  pi.on("session_shutdown", () => {
    if (requestPoller) clearInterval(requestPoller);
    if (initialPoller) clearTimeout(initialPoller);
    requestPoller = undefined;
    initialPoller = undefined;
  });

  pi.on("resources_discover", () => ({
    skillPaths: profileSkillPaths(activeProfile, plugins, skillCatalog),
  }));

  pi.on("before_agent_start", (event) => ({
    systemPrompt: `${event.systemPrompt}\n\n${profileContext(activeProfile)}`,
  }));

  pi.registerTool({
    name: "get_vertical_workflow_plan",
    label: "Get Vertical Workflow Plan",
    description: "获取当前岗位某项服务的确定性 DAG 执行计划；选择服务后、执行任务前必须调用。",
    parameters: Type.Object({
      service_id: Type.String({ description: "当前 Profile 中的服务 ID" }),
    }),
    async execute(_toolCallId, params) {
      const service = activeProfile.services.find((item) => item.id === params.service_id);
      if (!service) throw new Error(`Service ${params.service_id} is not available in ${activeProfile.id}`);
      const workflow = workflows.get(service.workflow);
      if (!workflow) throw new Error(`Workflow ${service.workflow} is not installed`);
      return {
        content: [{ type: "text", text: renderPlan(workflow) }],
        details: { profile: activeProfile.id, service: service.id, workflow: workflow.id },
      };
    },
  });

  pi.registerTool({
    name: "director_propose_write_intent",
    label: "Prepare Exact Write for Approval",
    description: "冻结知识库或销售台账的一批具体变更，并生成审批绑定哈希；此工具本身不写业务数据。",
    parameters: Type.Object({
      logical_tool: Type.Union([Type.Literal("knowledge.write"), Type.Literal("sales.write")]),
      payload: Type.Unknown({ description: "必须与后续对应 director_*_write 工具的完整参数完全一致" }),
    }),
    async execute(_toolCallId, params) {
      consumeExternalDecision();
      if (!activeTask || isTerminal(activeTask)) throw new Error("当前会话没有运行中的受管任务");
      const previous = activeTask;
      const workflow = workflowFor(previous);
      const next = proposeWriteIntent(
        previous,
        workflow as RuntimeWorkflow,
        params.logical_tool,
        params.payload,
        previous.version,
      );
      persistTransition(previous, next);
      return {
        content: [{ type: "text", text: `已冻结待审批变更。\n${renderRuntimeState(next, workflow)}` }],
        details: {
          task_id: next.task_id,
          intent_id: next.pending_write!.intent_id,
          payload_sha256: next.pending_write!.payload_sha256,
        },
      };
    },
  });

  pi.registerTool({
    name: "director_complete_node",
    label: "Complete Current Workflow Node",
    description: "记录当前 DAG 的 agent 或 validator 节点已完成。不能完成 tool、approval 或其他节点。",
    parameters: Type.Object({
      node_id: Type.String({ description: "必须是当前阶段中的 agent/validator 节点 ID" }),
      summary: Type.Optional(Type.String({ description: "完成证据或产物摘要" })),
    }),
    async execute(_toolCallId, params) {
      consumeExternalDecision();
      if (!activeTask || isTerminal(activeTask)) throw new Error("当前会话没有运行中的受管任务");
      const previous = activeTask;
      const workflow = workflowFor(previous);
      const next = completeModelNode(
        previous,
        workflow as RuntimeWorkflow,
        params.node_id,
        previous.version,
        params.summary,
      );
      persistTransition(previous, next);
      return {
        content: [{ type: "text", text: renderRuntimeState(next, workflow) }],
        details: { task_id: next.task_id, version: next.version, status: next.status },
      };
    },
  });

  pi.on("tool_call", (event) => {
    try {
      consumeExternalDecision();
    } catch (error) {
      return { block: true, reason: `外部任务状态同步失败：${(error as Error).message}` };
    }
    const toolName = event.toolName;
    const input = event.input;
    if ((toolName === "write" || toolName === "edit") && inputTargetsData(input, projectRoot)) {
      return {
        block: true,
        reason: "data/ 下的结构化数据只能通过 director_knowledge_* 或 director_sales_* 适配器修改。",
      };
    }
    if (!activeTask || isTerminal(activeTask)) return;
    const activeNodes = currentNodes(activeTask, workflowFor(activeTask) as RuntimeWorkflow);
    if (!MANAGED_ALLOWED_TOOLS.has(toolName)) {
      return {
        block: true,
        reason: `受管工作流未授权工具 ${toolName}；请使用当前 DAG 声明的适配器或只读工具。`,
      };
    }
    if (toolName === "bash") {
      return { block: true, reason: "受管工作流运行期间禁用 bash；请使用当前 DAG 的确定性工具或节点完成工具。" };
    }
    if (MANAGED_READ_TOOLS.has(toolName) && activeNodes.some((node) => node.type === "tool")) {
      return { block: true, reason: "当前阶段包含确定性 Tool 节点，只允许调用与 DAG 匹配的 director_* 适配器。" };
    }
    if (MANAGED_READ_TOOLS.has(toolName) && !inputTargetsSafeProjectRead(input, projectRoot)) {
      return {
        block: true,
        reason: "受管任务只允许显式读取项目内非敏感路径；禁止绝对路径、data/.pi/.git、密钥文件和链接穿透。",
      };
    }
    if ((toolName === "write" || toolName === "edit") && !inputTargetsOnlyOutputs(input, projectRoot)) {
      return { block: true, reason: "受管任务的普通文件写入只允许位于 outputs/；业务数据必须使用受控适配器。" };
    }
    if (
      (toolName === "write" || toolName === "edit") &&
      !activeNodes.some((node) =>
        node.permissions?.includes("artifact.write"),
      )
    ) {
      return { block: true, reason: "当前 DAG 阶段没有 artifact.write 权限，不能生成或修改产物。" };
    }
    if (
      activeTask.status === "waiting_approval" &&
      (toolName === "write" ||
        toolName === "edit" ||
        toolName === "director_propose_write_intent" ||
        toolName === "director_complete_node" ||
        toolName === "director_knowledge_write" ||
        toolName === "director_sales_write")
    ) {
      return { block: true, reason: "任务正在等待用户通过 /director-approve 或 /director-reject 作出决定。" };
    }
    if (toolName === "director_complete_node") {
      const nodeId =
        event.input && typeof event.input === "object"
          ? (event.input as Record<string, unknown>).node_id
          : undefined;
      if (typeof nodeId !== "string" || !activeTask) {
        return { block: true, reason: "节点完成请求缺少有效 node_id 或当前任务。" };
      }
      const workflow = workflowFor(activeTask);
      const node = currentNodes(activeTask, workflow as RuntimeWorkflow).find(
        (candidate) => candidate.id === nodeId,
      );
      if (!node || (node.type !== "agent" && node.type !== "validator")) {
        return { block: true, reason: "只能完成当前阶段的 agent/validator 节点。" };
      }
    }
    const logicalTool = ADAPTER_TO_LOGICAL_TOOL.get(toolName);
    if (logicalTool) {
      try {
        requireLogicalTool(logicalTool);
      } catch (error) {
        return { block: true, reason: (error as Error).message };
      }
    }
  });

  pi.registerCommand("director-profile", {
    description: "查看或切换市场总监/产品总监角色",
    handler: async (args, ctx) => {
      let selected = args.trim();
      if (!selected && ctx.hasUI) {
        selected =
          (await ctx.ui.select(
            "选择垂直角色",
            [...profiles.values()].map((profile) => profile.id),
          )) ?? "";
      }
      if (!selected) {
        ctx.ui.notify(`当前角色：${activeProfile.id}`, "info");
        return;
      }
      if (activeTask && !isTerminal(activeTask)) {
        ctx.ui.notify("当前有运行中的受管任务；请先完成或 /director-cancel 后再切换角色。", "error");
        return;
      }
      const profile = profiles.get(selected);
      if (!profile) {
        ctx.ui.notify(`未知角色：${selected}。可选：${[...profiles.keys()].join(", ")}`, "error");
        return;
      }
      activeProfile = profile;
      process.env.WORKFLOW_AGENT_PROFILE = activeProfile.id;
      ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}`);
      ctx.ui.notify(`已切换到${activeProfile.display_name}，正在重载对应 Skills`, "info");
      await ctx.reload();
    },
  });

  pi.registerCommand("director-apply-profile-switch", {
    description: "内部命令：为本地工作台请求安全切换 Profile",
    handler: async (args, ctx) => {
      const profileId = args.trim();
      const profile = profiles.get(profileId);
      if (!profile || !profileSwitchQueued) {
        profileSwitchQueued = false;
        ctx.ui.notify("Profile 自动切换请求无效，已拒绝。", "error");
        return;
      }
      if (activeTask && !isTerminal(activeTask)) {
        profileSwitchQueued = false;
        ctx.ui.notify("活动任务运行期间不能切换 Profile。", "error");
        return;
      }
      activeProfile = profile;
      process.env.WORKFLOW_AGENT_PROFILE = profile.id;
      ctx.ui.setStatus("vertical-workflow", `正在切换角色：${profile.display_name}`);
      ctx.ui.notify(`工作台请求属于${profile.display_name}，正在自动切换并重载对应 Skills。`, "info");
      await ctx.reload();
    },
  });

  pi.registerCommand("director-services", {
    description: "列出当前角色可直接使用的服务",
    handler: async (_args, ctx) => {
      ctx.ui.notify(renderServices(activeProfile), "info");
    },
  });

  pi.registerCommand("director-run", {
    description: "按服务启动工作流：/director-run <服务ID> <任务>",
    handler: async (args, ctx) => {
      if (activeTask && !isTerminal(activeTask)) {
        ctx.ui.notify(`当前已有运行中的任务 ${activeTask.task_id}；请先完成或取消。`, "error");
        return;
      }
      const trimmed = args.trim();
      const separator = trimmed.indexOf(" ");
      const serviceId = separator < 0 ? trimmed : trimmed.slice(0, separator);
      let request = separator < 0 ? "" : trimmed.slice(separator + 1).trim();
      const service = activeProfile.services.find((item) => item.id === serviceId);
      if (!service) {
        ctx.ui.notify(
          `请先给出有效服务 ID。可用服务：${activeProfile.services.map((item) => item.id).join(", ")}`,
          "error",
        );
        return;
      }
      if (!request && ctx.hasUI) {
        request = (await ctx.ui.input(`${service.display_name}：请描述任务`))?.trim() ?? "";
      }
      if (!request) {
        ctx.ui.notify("任务内容不能为空。", "error");
        return;
      }
      const workflow = workflows.get(service.workflow);
      if (!workflow) {
        ctx.ui.notify(`工作流未安装：${service.workflow}`, "error");
        return;
      }
      const task = createTask({
        sessionKey,
        profileId: activeProfile.id,
        serviceId: service.id,
        workflow: workflow as RuntimeWorkflow,
        request,
      });
      try {
        persistNew(task);
      } catch (error) {
        ctx.ui.notify(`任务状态保存失败：${(error as Error).message}`, "error");
        return;
      }
      ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}｜任务：${task.status}`);
      sendTaskPrompt(task, service, workflow);
    },
  });

  pi.registerCommand("director-status", {
    description: "查看当前受管任务状态",
    handler: async (_args, ctx) => {
      try {
        consumeExternalDecision();
      } catch (error) {
        ctx.ui.notify(`外部任务状态同步失败：${(error as Error).message}`, "error");
        return;
      }
      if (!activeTask) {
        ctx.ui.notify("当前会话没有任务。", "info");
        return;
      }
      ctx.ui.notify(renderRuntimeState(activeTask, workflowFor(activeTask)), "info");
    },
  });

  pi.registerCommand("director-approve", {
    description: "批准当前人工关口：/director-approve [节点ID] [备注]",
    handler: async (args, ctx) => {
      try {
        consumeExternalDecision();
      } catch (error) {
        ctx.ui.notify(`外部任务状态同步失败：${(error as Error).message}`, "error");
        return;
      }
      if (!activeTask || activeTask.status !== "waiting_approval") {
        ctx.ui.notify("当前任务不在待审批状态。", "error");
        return;
      }
      const [requestedNode, ...noteParts] = args.trim().split(/\s+/).filter(Boolean);
      const nodeId = requestedNode || (activeTask.waiting_nodes.length === 1 ? activeTask.waiting_nodes[0] : "");
      if (!nodeId || !activeTask.waiting_nodes.includes(nodeId)) {
        ctx.ui.notify(`请指定待审批节点：${activeTask.waiting_nodes.join(", ")}`, "error");
        return;
      }
      const previous = activeTask;
      const workflow = workflowFor(previous);
      try {
        const next = approveNode(
          previous,
          workflow as RuntimeWorkflow,
          nodeId,
          previous.version,
          noteParts.join(" ") || undefined,
        );
        persistTransition(previous, next);
        ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}｜任务：${next.status}`);
        ctx.ui.notify(`已批准 ${nodeId}。\n${renderRuntimeState(next, workflow)}`, "info");
        if (!isTerminal(next)) {
          pi.sendUserMessage(`用户已批准节点 ${nodeId}。继续受管任务 ${next.task_id}，仅执行当前阶段节点。`);
        }
      } catch (error) {
        ctx.ui.notify(`审批失败：${(error as Error).message}`, "error");
      }
    },
  });

  pi.registerCommand("director-reject", {
    description: "拒绝当前人工关口：/director-reject [节点ID] [原因]",
    handler: async (args, ctx) => {
      try {
        consumeExternalDecision();
      } catch (error) {
        ctx.ui.notify(`外部任务状态同步失败：${(error as Error).message}`, "error");
        return;
      }
      if (!activeTask || activeTask.status !== "waiting_approval") {
        ctx.ui.notify("当前任务不在待审批状态。", "error");
        return;
      }
      const [requestedNode, ...noteParts] = args.trim().split(/\s+/).filter(Boolean);
      const nodeId = requestedNode || (activeTask.waiting_nodes.length === 1 ? activeTask.waiting_nodes[0] : "");
      if (!nodeId || !activeTask.waiting_nodes.includes(nodeId)) {
        ctx.ui.notify(`请指定待审批节点：${activeTask.waiting_nodes.join(", ")}`, "error");
        return;
      }
      const previous = activeTask;
      const workflow = workflowFor(previous);
      try {
        const next = rejectApproval(
          previous,
          workflow as RuntimeWorkflow,
          nodeId,
          previous.version,
          noteParts.join(" ") || undefined,
        );
        persistTransition(previous, next);
        ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}`);
        ctx.ui.notify(`已拒绝 ${nodeId}，任务已终止。`, "warning");
      } catch (error) {
        ctx.ui.notify(`拒绝失败：${(error as Error).message}`, "error");
      }
    },
  });

  pi.registerCommand("director-cancel", {
    description: "取消当前受管任务：/director-cancel [原因]",
    handler: async (args, ctx) => {
      try {
        consumeExternalDecision();
      } catch (error) {
        ctx.ui.notify(`外部任务状态同步失败：${(error as Error).message}`, "error");
        return;
      }
      if (!activeTask || isTerminal(activeTask)) {
        ctx.ui.notify("当前没有运行中的任务。", "info");
        return;
      }
      const previous = activeTask;
      try {
        const next = cancelTask(previous, previous.version, args.trim() || undefined);
        persistTransition(previous, next);
        ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}`);
        ctx.ui.notify(`任务 ${next.task_id} 已取消。`, "warning");
      } catch (error) {
        ctx.ui.notify(`取消失败：${(error as Error).message}`, "error");
      }
    },
  });
}

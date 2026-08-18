import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";

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
  id: string;
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

function loadPluginBundle(): PluginBundle {
  const plugins = new Map<string, PluginManifest>();
  const workflows = new Map<string, Workflow>();
  for (const manifestPath of findPluginManifests(join(packageRoot, "vertical_plugins"))) {
    const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as PluginManifest;
    if (
      !manifest.id ||
      !Array.isArray(manifest.dependencies) ||
      !Array.isArray(manifest.skills) ||
      !Array.isArray(manifest.workflows)
    ) {
      throw new Error(`Invalid plugin manifest ${manifestPath}`);
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
      if (workflow.plugin !== manifest.id) {
        throw new Error(`Workflow ${workflow.id} must belong to ${manifest.id}`);
      }
      if (workflows.has(workflow.id)) throw new Error(`Duplicate workflow ${workflow.id}`);
      const nodeIds = workflow.nodes.map((node) => node.id);
      if (nodeIds.length !== new Set(nodeIds).size) {
        throw new Error(`Workflow ${workflow.id} contains duplicate nodes`);
      }
      planWorkflow(workflow);
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
    for (const dependency of plugin.dependencies) visit(dependency.id);
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
    "到达 approval 节点时必须停止并请求用户确认；未确认前不得执行后续阶段。subagent 必须遵守计划中的 objective、allowed_tools、max_turns 和 write_scope。",
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

  pi.on("session_start", (_event, ctx) => {
    ctx.ui.setStatus("vertical-workflow", `角色：${activeProfile.display_name}`);
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

  pi.registerCommand("director-services", {
    description: "列出当前角色可直接使用的服务",
    handler: async (_args, ctx) => {
      ctx.ui.notify(renderServices(activeProfile), "info");
    },
  });

  pi.registerCommand("director-run", {
    description: "按服务启动工作流：/director-run <服务ID> <任务>",
    handler: async (args, ctx) => {
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
      pi.sendUserMessage(
        `/skill:${service.skill} 当前角色：${activeProfile.display_name}。严格按以下 DAG 计划执行；到达 human_gate 必须暂停并请求我确认，subagent 不得越过 boundary。\n${renderPlan(workflow)}\n用户任务：${request}`,
        { expandPromptTemplates: true },
      );
    },
  });
}

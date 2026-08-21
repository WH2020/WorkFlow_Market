from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


NODE_TYPES = {"agent", "tool", "subagent", "approval", "parallel", "join", "validator"}
FORBIDDEN_PROFILE_TERMS = ("wechat", "weflow", "微信")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TOOL_REQUIRED_PERMISSIONS = {
    "knowledge.search": {"knowledge.read"},
    "knowledge.write": {"knowledge.write"},
    "sales.read": {"sales.read"},
    "sales.write": {"sales.write"},
    "account.search": {"sales.read"},
    "account.read_360": {"sales.read"},
    "signals.read": {"sales.read"},
    "web.search": {"web.read"},
    "web.open": {"web.read"},
    "pdf.read": {"knowledge.read"},
    "presentation.plan.write": {"presentation.plan.write"},
    "weekly.snapshot": {"knowledge.read", "sales.read", "task.audit.read", "artifact.read"},
    "artifact.deck.write": {"artifact.write"},
}
SUBAGENT_TOOL_REQUIRED_PERMISSIONS = {
    "web.search": {"web.read"},
    "web.open": {"web.read"},
}


class ManifestError(ValueError):
    """Raised when plugins or profiles cannot be composed safely."""


class WorkflowError(ValueError):
    """Raised when a workflow is not a valid bounded DAG."""


@dataclass(frozen=True)
class Plugin:
    manifest: dict[str, Any]
    path: Path
    workflows: tuple[dict[str, Any], ...]

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def version(self) -> str:
        return self.manifest["version"]


@dataclass
class ValidationReport:
    plugins: int = 0
    workflows: int = 0
    profiles: int = 0
    services: int = 0
    resolved_profiles: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "plugins": self.plugins,
            "workflows": self.workflows,
            "profiles": self.profiles,
            "services": self.services,
            "resolved_profiles": self.resolved_profiles,
        }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"JSON root must be an object: {path}")
    return value


def _require_text(data: dict[str, Any], key: str, source: Path | str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{source}: {key} must be a non-empty string")
    return value


def _semver(version: str, source: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(version)
    if not match:
        raise ManifestError(f"{source}: unsupported semantic version {version!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _satisfies(version: str, requirement: str, source: str) -> bool:
    actual = _semver(version, source)
    if requirement.startswith(">="):
        return actual >= _semver(requirement[2:], source)
    if requirement.startswith("=="):
        return actual == _semver(requirement[2:], source)
    if requirement.startswith("^"):
        wanted = _semver(requirement[1:], source)
        if wanted[0] > 0:
            upper = (wanted[0] + 1, 0, 0)
        elif wanted[1] > 0:
            upper = (0, wanted[1] + 1, 0)
        else:
            upper = (0, 0, wanted[2] + 1)
        return wanted <= actual < upper
    return actual == _semver(requirement, source)


def validate_workflow(workflow: dict[str, Any], plugin: Plugin | dict[str, Any]) -> None:
    plugin_manifest = plugin.manifest if isinstance(plugin, Plugin) else plugin
    plugin_id = _require_text(plugin_manifest, "id", "plugin")
    source = f"workflow {workflow.get('id', '<unknown>')}"
    workflow_id = _require_text(workflow, "id", source)
    _require_text(workflow, "display_name", source)
    if workflow.get("plugin") != plugin_id:
        raise WorkflowError(f"{workflow_id}: plugin must be {plugin_id!r}")

    nodes = workflow.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowError(f"{workflow_id}: nodes must be a non-empty array")
    node_map: dict[str, dict[str, Any]] = {}
    declared_permissions = set(plugin_manifest.get("permissions", []))
    declared_skills = set(plugin_manifest.get("skills", []))
    for node in nodes:
        if not isinstance(node, dict):
            raise WorkflowError(f"{workflow_id}: every node must be an object")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise WorkflowError(f"{workflow_id}: node id must be a non-empty string")
        if node_id in node_map:
            raise WorkflowError(f"{workflow_id}: duplicate node id {node_id!r}")
        node_type = node.get("type")
        if node_type not in NODE_TYPES:
            raise WorkflowError(f"{workflow_id}/{node_id}: unsupported node type {node_type!r}")
        if "permissions" not in node:
            raise WorkflowError(f"{workflow_id}/{node_id}: permissions is required")
        permissions = node["permissions"]
        if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
            raise WorkflowError(f"{workflow_id}/{node_id}: permissions must be a string array")
        excess = set(permissions) - declared_permissions
        if excess:
            raise WorkflowError(
                f"{workflow_id}/{node_id}: permissions not declared by plugin: {sorted(excess)}"
            )
        structured_writes = {
            permission
            for permission in permissions
            if permission.endswith(".write") and permission != "artifact.write"
        }
        if structured_writes and node_type != "tool":
            raise WorkflowError(
                f"{workflow_id}/{node_id}: structured writes require a tool node: "
                f"{sorted(structured_writes)}"
            )
        if node_type == "agent":
            if not isinstance(node.get("skill"), str) or not node["skill"].strip():
                raise WorkflowError(f"{workflow_id}/{node_id}: agent node requires skill")
            if node["skill"] not in declared_skills:
                raise WorkflowError(
                    f"{workflow_id}/{node_id}: skill {node['skill']!r} is not declared by plugin"
                )
        if node_type == "tool":
            if not isinstance(node.get("tool"), str) or not node["tool"].strip():
                raise WorkflowError(f"{workflow_id}/{node_id}: tool node requires tool")
            tool = node["tool"]
            if tool not in TOOL_REQUIRED_PERMISSIONS:
                raise WorkflowError(f"{workflow_id}/{node_id}: unknown logical tool {tool!r}")
            missing_permissions = TOOL_REQUIRED_PERMISSIONS[tool] - set(permissions)
            if missing_permissions:
                raise WorkflowError(
                    f"{workflow_id}/{node_id}: tool {tool!r} requires permissions "
                    f"{sorted(missing_permissions)}"
                )
        if node_type == "approval" and (not isinstance(node.get("policy"), str) or not node["policy"].strip()):
            raise WorkflowError(f"{workflow_id}/{node_id}: approval node requires policy")
        if node_type == "validator" and (not isinstance(node.get("check"), str) or not node["check"].strip()):
            raise WorkflowError(f"{workflow_id}/{node_id}: validator node requires check")
        if node_type == "subagent":
            boundary = node.get("boundary")
            if not isinstance(boundary, dict):
                raise WorkflowError(f"{workflow_id}/{node_id}: subagent requires boundary")
            objective = boundary.get("objective")
            tools = boundary.get("allowed_tools")
            max_turns = boundary.get("max_turns")
            write_scope = boundary.get("write_scope")
            if not isinstance(objective, str) or not objective.strip():
                raise WorkflowError(f"{workflow_id}/{node_id}: boundary.objective is required")
            if not isinstance(tools, list) or any(not isinstance(item, str) for item in tools):
                raise WorkflowError(f"{workflow_id}/{node_id}: boundary.allowed_tools must be a string array")
            unknown_tools = set(tools) - set(SUBAGENT_TOOL_REQUIRED_PERMISSIONS)
            if unknown_tools:
                raise WorkflowError(
                    f"{workflow_id}/{node_id}: unsupported subagent tools {sorted(unknown_tools)}"
                )
            required_tool_permissions: set[str] = set()
            for tool in tools:
                required_tool_permissions.update(SUBAGENT_TOOL_REQUIRED_PERMISSIONS[tool])
            missing_tool_permissions = required_tool_permissions - set(permissions)
            if missing_tool_permissions:
                raise WorkflowError(
                    f"{workflow_id}/{node_id}: subagent tools require permissions "
                    f"{sorted(missing_tool_permissions)}"
                )
            if not isinstance(max_turns, int) or isinstance(max_turns, bool) or not 1 <= max_turns <= 20:
                raise WorkflowError(f"{workflow_id}/{node_id}: boundary.max_turns must be 1..20")
            if not isinstance(write_scope, list) or any(not isinstance(item, str) for item in write_scope):
                raise WorkflowError(f"{workflow_id}/{node_id}: boundary.write_scope must be a string array")
            for scope in write_scope:
                posix = PurePosixPath(scope)
                windows = PureWindowsPath(scope)
                if (
                    not scope.strip()
                    or scope.startswith("~")
                    or posix.is_absolute()
                    or windows.is_absolute()
                    or ".." in posix.parts
                    or ".." in windows.parts
                ):
                    raise WorkflowError(
                        f"{workflow_id}/{node_id}: unsafe subagent write scope {scope!r}"
                    )
            if write_scope:
                raise WorkflowError(
                    f"{workflow_id}/{node_id}: current subagent tool policy is read-only; "
                    "write_scope must be empty"
                )
        node_map[node_id] = node

    for node_id, node in node_map.items():
        if "depends_on" not in node:
            raise WorkflowError(f"{workflow_id}/{node_id}: depends_on is required")
        dependencies = node["depends_on"]
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise WorkflowError(f"{workflow_id}/{node_id}: depends_on must be a string array")
        if len(dependencies) != len(set(dependencies)):
            raise WorkflowError(f"{workflow_id}/{node_id}: duplicate dependency")
        missing = set(dependencies) - set(node_map)
        if missing:
            raise WorkflowError(f"{workflow_id}/{node_id}: unknown dependencies {sorted(missing)}")
        if node_id in dependencies:
            raise WorkflowError(f"{workflow_id}/{node_id}: node cannot depend on itself")
        if node.get("type") == "join" and len(dependencies) < 2:
            raise WorkflowError(f"{workflow_id}/{node_id}: join requires at least two dependencies")

    references_by_field: dict[str, list[str]] = {}
    for field_name in ("entry_nodes", "output_nodes"):
        references = workflow.get(field_name)
        if not isinstance(references, list) or not references:
            raise WorkflowError(f"{workflow_id}: {field_name} must be a non-empty array")
        missing = set(references) - set(node_map)
        if missing:
            raise WorkflowError(f"{workflow_id}: {field_name} has unknown nodes {sorted(missing)}")
        if len(references) != len(set(references)):
            raise WorkflowError(f"{workflow_id}: {field_name} contains duplicates")
        references_by_field[field_name] = references

    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            raise WorkflowError(f"{workflow_id}: DAG cycle detected at {node_id!r}")
        if state.get(node_id) == 2:
            return
        state[node_id] = 1
        for dependency in node_map[node_id].get("depends_on", []):
            visit(dependency)
        state[node_id] = 2

    for node_id in node_map:
        visit(node_id)

    for node_id, node in node_map.items():
        if node.get("type") != "tool" or node.get("tool") not in {
            "knowledge.write", "sales.write", "artifact.deck.write"
        }:
            continue
        dependencies = node.get("depends_on", [])
        direct_approvals = [
            dependency for dependency in dependencies
            if node_map[dependency].get("type") == "approval"
        ]
        if len(dependencies) != 1 or len(direct_approvals) != 1:
            raise WorkflowError(
                f"{workflow_id}/{node_id}: structured write tool requires exactly one direct approval"
            )
        approval_id = direct_approvals[0]
        approval_node = node_map[approval_id]
        approval_dependencies = approval_node.get("depends_on", [])
        if (
            len(approval_dependencies) != 1
            or node_map[approval_dependencies[0]].get("type") not in {"agent", "validator"}
        ):
            raise WorkflowError(
                f"{workflow_id}/{approval_id}: write approval requires one direct agent/validator predecessor"
            )
        protected_writes = [
            candidate for candidate in node_map.values()
            if candidate.get("type") == "tool"
            and candidate.get("tool") in {"knowledge.write", "sales.write", "artifact.deck.write"}
            and approval_id in candidate.get("depends_on", [])
        ]
        if len(protected_writes) != 1:
            raise WorkflowError(
                f"{workflow_id}/{approval_id}: approval must protect exactly one direct structured write"
            )

    actual_entries = {node_id for node_id, node in node_map.items() if not node["depends_on"]}
    declared_entries = set(references_by_field["entry_nodes"])
    if actual_entries != declared_entries:
        raise WorkflowError(
            f"{workflow_id}: entry_nodes must equal dependency-free nodes; "
            f"expected {sorted(actual_entries)}"
        )
    successors: dict[str, set[str]] = {node_id: set() for node_id in node_map}
    for node_id, node in node_map.items():
        for dependency in node["depends_on"]:
            successors[dependency].add(node_id)
    actual_outputs = {node_id for node_id, children in successors.items() if not children}
    declared_outputs = set(references_by_field["output_nodes"])
    if actual_outputs != declared_outputs:
        raise WorkflowError(
            f"{workflow_id}: output_nodes must equal terminal nodes; expected {sorted(actual_outputs)}"
        )


class Platform:
    """Loads, validates and composes plugin bundles without running model code."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.plugin_root = self.root / "vertical_plugins"
        self.profile_root = self.root / "profiles"
        self._plugins: dict[str, Plugin] | None = None
        self._profiles: dict[str, tuple[dict[str, Any], Path]] | None = None

    def load_plugins(self) -> dict[str, Plugin]:
        registry: dict[str, Plugin] = {}
        workflow_owners: dict[str, str] = {}
        for manifest_path in sorted(self.plugin_root.rglob("plugin.json")):
            manifest = _read_json(manifest_path)
            plugin_id = _require_text(manifest, "id", manifest_path)
            version = _require_text(manifest, "version", manifest_path)
            _require_text(manifest, "display_name", manifest_path)
            _require_text(manifest, "description", manifest_path)
            if manifest.get("category") not in {"shared", "market", "product"}:
                raise ManifestError(f"{manifest_path}: unsupported category {manifest.get('category')!r}")
            serialized = json.dumps(manifest, ensure_ascii=False).lower()
            forbidden = [term for term in FORBIDDEN_PROFILE_TERMS if term in serialized]
            if forbidden:
                raise ManifestError(
                    f"{manifest_path}: forbidden chat integration reference: {forbidden}"
                )
            _semver(version, str(manifest_path))
            if manifest.get("api_version") != "1.0":
                raise ManifestError(f"{manifest_path}: api_version must be '1.0'")
            if plugin_id in registry:
                raise ManifestError(
                    f"duplicate plugin id {plugin_id!r}: {registry[plugin_id].path} and {manifest_path}"
                )
            for field_name in ("permissions", "skills", "workflows"):
                if field_name not in manifest:
                    raise ManifestError(f"{manifest_path}: {field_name} is required")
                value = manifest.get(field_name, [])
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    raise ManifestError(f"{manifest_path}: {field_name} must be a string array")
                if len(value) != len(set(value)):
                    raise ManifestError(f"{manifest_path}: duplicate values in {field_name}")
            if "dependencies" not in manifest:
                raise ManifestError(f"{manifest_path}: dependencies is required")
            dependencies = manifest["dependencies"]
            if not isinstance(dependencies, list):
                raise ManifestError(f"{manifest_path}: dependencies must be an array")
            seen_dependencies: set[str] = set()
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise ManifestError(f"{manifest_path}: dependency must be an object")
                dependency_id = _require_text(dependency, "id", manifest_path)
                _require_text(dependency, "version", manifest_path)
                if dependency_id in seen_dependencies:
                    raise ManifestError(f"{manifest_path}: duplicate dependency {dependency_id!r}")
                seen_dependencies.add(dependency_id)

            workflows: list[dict[str, Any]] = []
            for relative_path in manifest.get("workflows", []):
                workflow_path = (manifest_path.parent / relative_path).resolve()
                if manifest_path.parent.resolve() not in workflow_path.parents:
                    raise ManifestError(f"{manifest_path}: workflow escapes plugin directory")
                workflows.append(_read_json(workflow_path))
            plugin = Plugin(manifest, manifest_path, tuple(workflows))
            for workflow in workflows:
                validate_workflow(workflow, plugin)
                workflow_id = workflow["id"]
                if workflow_id in workflow_owners:
                    raise ManifestError(
                        f"duplicate workflow id {workflow_id!r}: "
                        f"{workflow_owners[workflow_id]} and {plugin_id}"
                    )
                workflow_owners[workflow_id] = plugin_id
            registry[plugin_id] = plugin

        if not registry:
            raise ManifestError(f"No plugin manifests found under {self.plugin_root}")
        self._validate_plugin_registry(registry)
        self._plugins = registry
        return registry

    def _validate_plugin_registry(self, registry: dict[str, Plugin]) -> None:
        state: dict[str, int] = {}

        def visit(plugin_id: str) -> None:
            if state.get(plugin_id) == 1:
                raise ManifestError(f"plugin dependency cycle detected at {plugin_id!r}")
            if state.get(plugin_id) == 2:
                return
            state[plugin_id] = 1
            for dependency in registry[plugin_id].manifest.get("dependencies", []):
                dependency_id = dependency["id"]
                if dependency_id not in registry:
                    raise ManifestError(
                        f"missing plugin {dependency_id!r}, requested by {plugin_id}"
                    )
                visit(dependency_id)
                actual = registry[dependency_id].version
                requirement = dependency["version"]
                if not _satisfies(actual, requirement, f"dependency {plugin_id} -> {dependency_id}"):
                    raise ManifestError(
                        f"{plugin_id}: {dependency_id} {actual} does not satisfy {requirement}"
                    )
            state[plugin_id] = 2

        for plugin_id in registry:
            visit(plugin_id)

    def load_profiles(self) -> dict[str, tuple[dict[str, Any], Path]]:
        profiles: dict[str, tuple[dict[str, Any], Path]] = {}
        for profile_path in sorted(self.profile_root.glob("*/profile.json")):
            profile = _read_json(profile_path)
            profile_id = _require_text(profile, "id", profile_path)
            if profile_id in profiles:
                raise ManifestError(f"duplicate profile id {profile_id!r}")
            self._validate_profile_shape(profile, profile_path)
            profiles[profile_id] = (profile, profile_path)
        if not profiles:
            raise ManifestError(f"No profiles found under {self.profile_root}")
        self._profiles = profiles
        return profiles

    def _validate_profile_shape(self, profile: dict[str, Any], source: Path) -> None:
        serialized = json.dumps(profile, ensure_ascii=False).lower()
        forbidden = [term for term in FORBIDDEN_PROFILE_TERMS if term in serialized]
        if forbidden:
            raise ManifestError(f"{source}: forbidden chat integration reference: {forbidden}")
        for field_name in ("display_name", "description", "default_service"):
            _require_text(profile, field_name, source)
        plugins = profile.get("plugins")
        if not isinstance(plugins, list) or not plugins or any(not isinstance(item, str) for item in plugins):
            raise ManifestError(f"{source}: plugins must be a non-empty string array")
        if len(plugins) != len(set(plugins)):
            raise ManifestError(f"{source}: duplicate plugin in profile")
        services = profile.get("services")
        if not isinstance(services, list) or not services:
            raise ManifestError(f"{source}: services must be a non-empty array")
        service_ids: set[str] = set()
        for service in services:
            if not isinstance(service, dict):
                raise ManifestError(f"{source}: every service must be an object")
            for field_name in ("id", "display_name", "description", "workflow", "skill"):
                _require_text(service, field_name, source)
            if service["id"] in service_ids:
                raise ManifestError(f"{source}: duplicate service id {service['id']!r}")
            service_ids.add(service["id"])
        if profile["default_service"] not in service_ids:
            raise ManifestError(f"{source}: default_service must reference a service id")

    def resolve_profile(self, profile_id: str) -> dict[str, Any]:
        plugins = self._plugins or self.load_plugins()
        profiles = self._profiles or self.load_profiles()
        if profile_id not in profiles:
            raise ManifestError(f"Unknown profile {profile_id!r}")
        profile, source = profiles[profile_id]
        ordered: list[str] = []
        state: dict[str, int] = {}

        def visit(plugin_id: str, requested_by: str) -> None:
            if plugin_id not in plugins:
                raise ManifestError(f"{source}: missing plugin {plugin_id!r}, requested by {requested_by}")
            if state.get(plugin_id) == 1:
                raise ManifestError(f"plugin dependency cycle detected at {plugin_id!r}")
            if state.get(plugin_id) == 2:
                return
            state[plugin_id] = 1
            plugin = plugins[plugin_id]
            for dependency in plugin.manifest.get("dependencies", []):
                dependency_id = dependency["id"]
                visit(dependency_id, plugin_id)
                actual = plugins[dependency_id].version
                requirement = dependency["version"]
                if not _satisfies(actual, requirement, f"dependency {plugin_id} -> {dependency_id}"):
                    raise ManifestError(
                        f"{plugin_id}: {dependency_id} {actual} does not satisfy {requirement}"
                    )
            state[plugin_id] = 2
            ordered.append(plugin_id)

        for plugin_id in profile["plugins"]:
            visit(plugin_id, profile_id)

        workflow_ids: set[str] = set()
        skill_ids: set[str] = set()
        for plugin_id in ordered:
            plugin = plugins[plugin_id]
            skill_ids.update(plugin.manifest.get("skills", []))
            for workflow in plugin.workflows:
                workflow_id = workflow["id"]
                if workflow_id in workflow_ids:
                    raise ManifestError(f"duplicate workflow id in profile {profile_id}: {workflow_id!r}")
                workflow_ids.add(workflow_id)
        for service in profile["services"]:
            if service["workflow"] not in workflow_ids:
                raise ManifestError(
                    f"{source}: service {service['id']!r} references unavailable workflow {service['workflow']!r}"
                )
            if service["skill"] not in skill_ids:
                raise ManifestError(
                    f"{source}: service {service['id']!r} references unavailable skill {service['skill']!r}"
                )
        return {"profile": profile, "resolved_plugins": ordered}

    def validate_all(self) -> ValidationReport:
        plugins = self.load_plugins()
        profiles = self.load_profiles()
        report = ValidationReport(
            plugins=len(plugins),
            workflows=sum(len(plugin.workflows) for plugin in plugins.values()),
            profiles=len(profiles),
            services=sum(len(profile["services"]) for profile, _ in profiles.values()),
        )
        for profile_id in profiles:
            result = self.resolve_profile(profile_id)
            report.resolved_profiles[profile_id] = result["resolved_plugins"]
        return report

    def list_services(self, profile_id: str | None = None) -> list[dict[str, Any]]:
        self.validate_all()
        profiles = self._profiles or {}
        selected: Iterable[str] = [profile_id] if profile_id else sorted(profiles)
        services: list[dict[str, Any]] = []
        for selected_id in selected:
            if selected_id not in profiles:
                raise ManifestError(f"Unknown profile {selected_id!r}")
            profile, _ = profiles[selected_id]
            services.extend({"profile": selected_id, **service} for service in profile["services"])
        return services

    def plan_workflow(self, workflow_id: str, profile_id: str | None = None) -> dict[str, Any]:
        """Return deterministic dependency layers without invoking tools or models."""
        self.validate_all()
        plugins = self._plugins or {}
        allowed_plugins: set[str] | None = None
        if profile_id:
            allowed_plugins = set(self.resolve_profile(profile_id)["resolved_plugins"])

        selected: dict[str, Any] | None = None
        owner: str | None = None
        for plugin_id, plugin in plugins.items():
            for workflow in plugin.workflows:
                if workflow["id"] == workflow_id:
                    selected = workflow
                    owner = plugin_id
                    break
            if selected is not None:
                break
        if selected is None:
            raise ManifestError(f"Unknown workflow {workflow_id!r}")
        if allowed_plugins is not None and owner not in allowed_plugins:
            raise ManifestError(
                f"Workflow {workflow_id!r} is not available in profile {profile_id!r}"
            )

        nodes = {node["id"]: node for node in selected["nodes"]}
        remaining = set(nodes)
        completed: set[str] = set()
        stages: list[dict[str, Any]] = []
        while remaining:
            ready = sorted(
                node_id
                for node_id in remaining
                if set(nodes[node_id].get("depends_on", [])) <= completed
            )
            if not ready:  # Defensive: validate_all already rejects cycles.
                raise WorkflowError(f"{workflow_id}: cannot produce execution plan")
            planned_nodes: list[dict[str, Any]] = []
            for node_id in ready:
                planned = copy.deepcopy(nodes[node_id])
                planned["gate"] = planned["type"] == "approval"
                planned["execution_mode"] = {
                    "approval": "human_gate",
                    "subagent": "bounded_subagent",
                }.get(planned["type"], "automatic")
                planned_nodes.append(planned)
            stages.append({"index": len(stages), "nodes": planned_nodes})
            remaining.difference_update(ready)
            completed.update(ready)

        return {
            "status": "ok",
            "workflow": workflow_id,
            "plugin": owner,
            "profile": profile_id,
            "entry_nodes": copy.deepcopy(selected["entry_nodes"]),
            "output_nodes": copy.deepcopy(selected["output_nodes"]),
            "stages": stages,
        }

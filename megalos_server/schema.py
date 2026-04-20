"""YAML workflow schema parsing."""

import os
import re
from typing import TYPE_CHECKING

import jsonschema
import yaml
from pathlib import Path

from .errors import YAML_MAX

if TYPE_CHECKING:
    from .mcp_registry import Registry


REQUIRED_STEP_KEYS = {"id", "title", "directive_template", "gates", "anti_patterns"}
# Subset required for non-LLM step types (e.g. `action: mcp_tool_call`).
# LLM-prompt fields are rejected for these steps by mutex rules.
_REQUIRED_STEP_KEYS_MCP = {"id", "title"}

_REF_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
# A ref-path string is a full-string match on `${step_data.<segments>}`.
# Anchored at start/end — any surrounding text counts as mixed interpolation.
_REF_STRING_RE = re.compile(r"^\$\{step_data\.[^}]+\}$")


def _is_valid_ref_path(ref: str) -> bool:
    if not ref.startswith("step_data."):
        return False
    parts = ref.split(".")
    if len(parts) < 2:
        return False
    for seg in parts[1:]:
        if not _REF_SEGMENT_RE.match(seg):
            return False
    return True


def _validate_mcp_arg_value(
    value: object, label: str, path: str, errors: list[str]
) -> None:
    """Recursively validate a value inside an mcp_tool_call `args` tree.

    Accepts scalars, nested dicts, nested lists. Strings that contain `${`
    must fully match the ref-path pattern `${step_data.<path>}`; otherwise
    they trip `mcp_tool_call_mixed_interpolation_not_supported`. Fully-matching
    ref-path strings are further validated against the same ref-path grammar
    used by preconditions (`step_data.<sid>[.<field>...]`).
    """
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                errors.append(
                    f"Step '{label}' args{path}: keys must be strings, "
                    f"got {type(k).__name__}"
                )
                continue
            _validate_mcp_arg_value(v, label, f"{path}.{k}", errors)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _validate_mcp_arg_value(item, label, f"{path}[{i}]", errors)
        return
    if isinstance(value, str):
        if "${" not in value:
            return  # literal string; nothing to validate
        if not _REF_STRING_RE.match(value):
            errors.append(
                f"Step '{label}' args{path}: mixed interpolation not supported "
                f"(value must be either a literal or a whole-string "
                f"'${{step_data.<ref>}}' — got {value!r}) "
                f"(code: mcp_tool_call_mixed_interpolation_not_supported)"
            )
            return
        inner = value[2:-1]  # strip ${ and }
        if not _is_valid_ref_path(inner):
            errors.append(
                f"Step '{label}' args{path}: ref-path is malformed "
                f"(value {value!r}); refs must match "
                f"'${{step_data.<sid>[.<field>...]}}' "
                f"(code: mcp_tool_call_invalid_ref_path)"
            )
        return
    # bool/int/float/None scalars are accepted as literals; no further check.
    if isinstance(value, (bool, int, float)) or value is None:
        return
    errors.append(
        f"Step '{label}' args{path}: unsupported value type "
        f"{type(value).__name__} (expected scalar, string, list, or mapping)"
    )


# Fields that must NOT appear on a step whose `action` is `mcp_tool_call`.
# Each entry pairs the forbidden field with the specific error code the plan
# spells out. Kept as a tuple of tuples rather than a dict so ordering is
# deterministic in error messages.
_MCP_MUTEX_FIELDS: tuple[tuple[str, str], ...] = (
    ("directive_template", "mcp_tool_call_with_directive_template"),
    ("gates", "mcp_tool_call_with_gates"),
    ("anti_patterns", "mcp_tool_call_with_anti_patterns"),
    ("call", "mcp_tool_call_with_call"),
    ("collect", "mcp_tool_call_with_collect"),
    ("output_schema", "mcp_tool_call_with_output_schema"),
)

_MCP_ALLOWED_STEP_KEYS: frozenset[str] = frozenset(
    {"id", "title", "action", "server", "tool", "args", "timeout",
     "step_description", "precondition", "branches", "default_branch"}
)


def _validate_mcp_tool_call_step(step: dict, label: str, errors: list[str]) -> None:
    """Validate a step whose `action` is `mcp_tool_call`.

    Handles: literal-only server/tool, recursive arg validation, mutex
    rejections, timeout bounds. Non-goals: runtime resolution (T02),
    registry cross-check (runs at workflow level).
    """
    # server
    if "server" not in step:
        errors.append(f"Step '{label}' (mcp_tool_call) missing required key 'server'")
    else:
        server = step["server"]
        if not isinstance(server, str) or not server:
            errors.append(
                f"Step '{label}' (mcp_tool_call) server must be a non-empty string"
            )
        elif "${" in server:
            errors.append(
                f"Step '{label}' (mcp_tool_call) server must be a literal string, "
                f"not an interpolation (got {server!r}) "
                f"(code: mcp_tool_call_server_not_literal)"
            )
    # tool
    if "tool" not in step:
        errors.append(f"Step '{label}' (mcp_tool_call) missing required key 'tool'")
    else:
        tool = step["tool"]
        if not isinstance(tool, str) or not tool:
            errors.append(
                f"Step '{label}' (mcp_tool_call) tool must be a non-empty string"
            )
        elif "${" in tool:
            errors.append(
                f"Step '{label}' (mcp_tool_call) tool must be a literal string, "
                f"not an interpolation (got {tool!r}) "
                f"(code: mcp_tool_call_tool_not_literal)"
            )
    # args
    if "args" not in step:
        errors.append(f"Step '{label}' (mcp_tool_call) missing required key 'args'")
    else:
        args = step["args"]
        if not isinstance(args, dict):
            errors.append(
                f"Step '{label}' (mcp_tool_call) args must be a mapping, "
                f"got {type(args).__name__}"
            )
        else:
            for k, v in args.items():
                if not isinstance(k, str):
                    errors.append(
                        f"Step '{label}' (mcp_tool_call) args keys must be strings"
                    )
                    continue
                _validate_mcp_arg_value(v, label, f".{k}", errors)
    # timeout
    if "timeout" in step:
        t = step["timeout"]
        if isinstance(t, bool) or not isinstance(t, (int, float)):
            errors.append(
                f"Step '{label}' (mcp_tool_call) timeout must be a number"
            )
        elif t <= 0:
            errors.append(
                f"Step '{label}' (mcp_tool_call) timeout must be positive"
            )
    # Mutex rejections
    for field, code in _MCP_MUTEX_FIELDS:
        if field in step:
            errors.append(
                f"Step '{label}' has both 'action: mcp_tool_call' and {field!r}; "
                f"mcp_tool_call steps are non-LLM and cannot declare this field "
                f"(code: {code})"
            )
    # Unknown fields — catches typos early. Kept strict like mcp_registry.
    unknown = set(step.keys()) - _MCP_ALLOWED_STEP_KEYS
    # Fields already flagged by mutex rules shouldn't double-report.
    unknown -= {f for f, _ in _MCP_MUTEX_FIELDS}
    if unknown:
        errors.append(
            f"Step '{label}' (mcp_tool_call) has unknown field(s) "
            f"{sorted(unknown)!r}"
        )


def _validate_step_optional_fields(step: dict, label: str, errors: list[str]) -> None:
    """Validate optional output_schema, max_retries, validation_hint on a step."""
    if "output_schema" in step:
        schema = step["output_schema"]
        if not isinstance(schema, dict):
            errors.append(f"Step '{label}' output_schema must be a JSON Schema object (dict)")
        else:
            try:
                jsonschema.Draft202012Validator.check_schema(schema)
            except jsonschema.SchemaError as e:
                errors.append(f"Step '{label}' output_schema is not valid JSON Schema: {e.message}")
    if "collect" in step:
        c = step["collect"]
        if not isinstance(c, bool):
            errors.append(f"Step '{label}' collect must be a boolean")
        elif c and "output_schema" not in step:
            errors.append(f"Step '{label}' has collect: true but is missing required 'output_schema'")
    if "step_description" in step:
        if not isinstance(step["step_description"], str):
            errors.append(f"Step '{label}' step_description must be a string")
    if "max_retries" in step:
        mr = step["max_retries"]
        if not isinstance(mr, int) or mr < 1:
            errors.append(f"Step '{label}' max_retries must be a positive integer")
    if "validation_hint" in step:
        if not isinstance(step["validation_hint"], str):
            errors.append(f"Step '{label}' validation_hint must be a string")
    if "inject_context" in step:
        ic = step["inject_context"]
        if not isinstance(ic, list):
            errors.append(f"Step '{label}' inject_context must be a list")
        else:
            for j, entry in enumerate(ic):
                if not isinstance(entry, dict):
                    errors.append(f"Step '{label}' inject_context[{j}] must be a mapping")
                    continue
                if "from" not in entry:
                    errors.append(f"Step '{label}' inject_context[{j}] missing required key 'from'")
                elif not isinstance(entry["from"], str):
                    errors.append(f"Step '{label}' inject_context[{j}] 'from' must be a string")
                if "fields" in entry and not isinstance(entry["fields"], list):
                    errors.append(f"Step '{label}' inject_context[{j}] 'fields' must be a list")
                if "summary" in entry and not isinstance(entry["summary"], bool):
                    errors.append(f"Step '{label}' inject_context[{j}] 'summary' must be a boolean")
    if "branches" in step:
        branches = step["branches"]
        if not isinstance(branches, list):
            errors.append(f"Step '{label}' branches must be a list")
        else:
            for j, branch in enumerate(branches):
                if not isinstance(branch, dict):
                    errors.append(f"Step '{label}' branches[{j}] must be a mapping")
                    continue
                if "next" not in branch or not isinstance(branch.get("next"), str):
                    errors.append(f"Step '{label}' branches[{j}] must have a 'next' string")
                if "condition" not in branch or not isinstance(branch.get("condition"), str):
                    errors.append(f"Step '{label}' branches[{j}] must have a 'condition' string")
    if "default_branch" in step:
        if not isinstance(step["default_branch"], str):
            errors.append(f"Step '{label}' default_branch must be a string")
    if "precondition" in step:
        pc = step["precondition"]
        if not isinstance(pc, dict):
            errors.append(f"Step '{label}' precondition must be a mapping")
        else:
            known = {"when_equals", "when_present"}
            keys = list(pc.keys())
            unknown = [k for k in keys if k not in known]
            if unknown:
                errors.append(f"Step '{label}' precondition has unknown predicate key(s): {sorted(unknown)}; valid: {sorted(known)}")
            present = [k for k in keys if k in known]
            if len(present) == 0:
                errors.append(f"Step '{label}' precondition must have exactly one of 'when_equals' or 'when_present'")
            elif len(present) > 1:
                errors.append(f"Step '{label}' precondition must have exactly one predicate, got: {sorted(present)}")
            elif present[0] == "when_equals":
                we = pc["when_equals"]
                if not isinstance(we, dict):
                    errors.append(f"Step '{label}' precondition.when_equals must be a mapping")
                else:
                    if "ref" not in we:
                        errors.append(f"Step '{label}' precondition.when_equals missing required key 'ref'")
                    elif not isinstance(we["ref"], str):
                        errors.append(f"Step '{label}' precondition.when_equals.ref must be a string")
                    elif not _is_valid_ref_path(we["ref"]):
                        errors.append(f"Step '{label}' precondition.when_equals.ref is not a valid ref-path: {we['ref']}")
                    if "value" not in we:
                        errors.append(f"Step '{label}' precondition.when_equals missing required key 'value'")
            elif present[0] == "when_present":
                wp = pc["when_present"]
                if not isinstance(wp, str):
                    errors.append(f"Step '{label}' precondition.when_present must be a string")
                elif not _is_valid_ref_path(wp):
                    errors.append(f"Step '{label}' precondition.when_present.ref is not a valid ref-path: {wp}")
    if "call" in step:
        call_val = step["call"]
        if not isinstance(call_val, str) or not call_val:
            errors.append(f"Step '{label}' call must be a non-empty string")
        # call + output_schema is allowed: validates child workflow's return value (M004 D14)
        if step.get("collect") is True:
            errors.append(
                f"Step '{label}' has both 'call' and 'collect: true'; "
                f"sub-workflow steps cannot also collect (code: call_with_collect)"
            )
        if "intermediate_artifacts" in step:
            errors.append(
                f"Step '{label}' has both 'call' and 'intermediate_artifacts'; "
                f"sub-workflow steps cannot also produce intermediate artifacts "
                f"(code: call_with_intermediate_artifacts)"
            )
        if "branches" in step and "default_branch" not in step:
            errors.append(
                f"Step '{label}' has both 'call' and 'branches' but no 'default_branch'; "
                f"post-hoc branch selection on sub-workflow return is not supported — "
                f"call-steps with branches must declare a default_branch "
                f"(code: call_branches_without_default)"
            )
    if "call_context_from" in step:
        ccf = step["call_context_from"]
        if not isinstance(ccf, str):
            errors.append(f"Step '{label}' call_context_from must be a string")
        else:
            if "call" not in step:
                errors.append(
                    f"Step '{label}' has 'call_context_from' without 'call' "
                    f"(code: call_context_from_without_call)"
                )
            if not _is_valid_ref_path(ccf):
                errors.append(
                    f"Step '{label}' call_context_from is not a valid ref-path: {ccf} "
                    f"(code: call_invalid_context_ref)"
                )
    if "directives" in step:
        d = step["directives"]
        if not isinstance(d, dict):
            errors.append(f"Step '{label}' directives must be a mapping")
        else:
            for key in ("tone", "strategy", "persona"):
                if key in d and not isinstance(d[key], str):
                    errors.append(f"Step '{label}' directives.{key} must be a string")
            if "constraints" in d:
                if not isinstance(d["constraints"], list):
                    errors.append(f"Step '{label}' directives.constraints must be a list")
                elif not all(isinstance(c, str) for c in d["constraints"]):
                    errors.append(f"Step '{label}' directives.constraints entries must be strings")
    if "intermediate_artifacts" in step:
        ia = step["intermediate_artifacts"]
        if not isinstance(ia, list):
            errors.append(f"Step '{label}' intermediate_artifacts must be a list")
        else:
            ia_ids = set()
            for j, art in enumerate(ia):
                if not isinstance(art, dict):
                    errors.append(f"Step '{label}' intermediate_artifacts[{j}] must be a mapping")
                    continue
                for rk in ("id", "description", "schema"):
                    if rk not in art:
                        errors.append(f"Step '{label}' intermediate_artifacts[{j}] missing '{rk}'")
                if "id" in art:
                    if not isinstance(art["id"], str):
                        errors.append(f"Step '{label}' intermediate_artifacts[{j}] 'id' must be a string")
                    else:
                        ia_ids.add(art["id"])
                if "description" in art and not isinstance(art["description"], str):
                    errors.append(f"Step '{label}' intermediate_artifacts[{j}] 'description' must be a string")
                if "schema" in art:
                    if not isinstance(art["schema"], dict):
                        errors.append(f"Step '{label}' intermediate_artifacts[{j}] 'schema' must be a dict")
                    else:
                        try:
                            jsonschema.Draft202012Validator.check_schema(art["schema"])
                        except jsonschema.SchemaError as e:
                            errors.append(f"Step '{label}' intermediate_artifacts[{j}] schema invalid: {e.message}")
                if "checkpoint" in art and not isinstance(art["checkpoint"], bool):
                    errors.append(f"Step '{label}' intermediate_artifacts[{j}] 'checkpoint' must be a boolean")
            # output_from cross-ref
            if "output_from" in step:
                of = step["output_from"]
                if not isinstance(of, str):
                    errors.append(f"Step '{label}' output_from must be a string")
                elif ia_ids and of not in ia_ids:
                    errors.append(f"Step '{label}' output_from '{of}' not found in intermediate_artifacts")
    elif "output_from" in step:
        errors.append(f"Step '{label}' has output_from without intermediate_artifacts")


def _precondition_ref(step: dict) -> str | None:
    """Extract the ref string from a step's precondition, or None if absent/malformed."""
    pc = step.get("precondition")
    if not isinstance(pc, dict):
        return None
    if "when_equals" in pc:
        we = pc["when_equals"]
        if isinstance(we, dict) and isinstance(we.get("ref"), str):
            return we["ref"]
    if "when_present" in pc:
        wp = pc["when_present"]
        if isinstance(wp, str):
            return wp
    return None


def _validate_workflow_preconditions(steps: list, errors: list[str]) -> None:
    """Cross-step precondition checks: (b) forward-ref, (c) first-step, (d) sub-path vs schemaless."""
    sid_to_index: dict[str, int] = {}
    sid_to_step: dict[str, dict] = {}
    for i, step in enumerate(steps):
        if isinstance(step, dict) and isinstance(step.get("id"), str):
            sid_to_index[step["id"]] = i
            sid_to_step[step["id"]] = step
    for k, step in enumerate(steps):
        if not isinstance(step, dict) or "precondition" not in step:
            continue
        ref = _precondition_ref(step)
        if ref is None or not _is_valid_ref_path(ref):
            continue
        label = step.get("id", str(k))
        # (c) first-step precondition — no valid prior step exists
        if k == 0:
            errors.append(
                f"Step '{label}' precondition is on the first step (index 0); "
                f"no prior step exists to reference"
            )
            continue
        parts = ref.split(".")
        if len(parts) < 2:
            continue
        ref_sid = parts[1]
        if ref_sid not in sid_to_index:
            continue
        m = sid_to_index[ref_sid]
        # (b) forward ref — ref points to a step at or after the current step
        if m >= k:
            errors.append(
                f"Step '{label}' precondition is a forward ref: references step '{ref_sid}' "
                f"(index {m}) which does not precede step '{label}' (index {k})"
            )
            continue
        # (d) sub-path against a step lacking output_schema and collect: true.
        # mcp_tool_call steps have an implicit envelope schema
        # ({"ok": bool, "value": ..., "error": {...}}), so sub-path refs into
        # them are always permitted.
        if len(parts) > 2:
            ref_step = sid_to_step[ref_sid]
            has_schema = "output_schema" in ref_step
            is_collect = ref_step.get("collect") is True
            is_mcp_tool_call = ref_step.get("action") == "mcp_tool_call"
            if not has_schema and not is_collect and not is_mcp_tool_call:
                errors.append(
                    f"Step '{label}' precondition sub-path references step '{ref_sid}' "
                    f"which lacks both 'output_schema' and 'collect: true'; "
                    f"sub-path refs require one of these"
                )


def validate_workflow_calls(workflows: dict[str, dict]) -> list[str]:
    """Cross-workflow checks: call targets exist; the call graph is acyclic.

    Returns a list of error messages (empty list = valid).
    """
    errors: list[str] = []
    # Build adjacency: parent_name -> [(step_id, target_name), ...]
    edges: dict[str, list[tuple[str, str]]] = {}
    for parent_name, wf in workflows.items():
        steps = wf.get("steps", [])
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or "call" not in step:
                continue
            target = step["call"]
            step_id = step.get("id", "?")
            if not isinstance(target, str):
                continue  # per-step parse already rejected this
            edges.setdefault(parent_name, []).append((step_id, target))
            if target not in workflows:
                errors.append(
                    f"Workflow '{parent_name}' step '{step_id}' calls unknown workflow "
                    f"'{target}' (code: unknown_call_target)"
                )
    # Cycle detection: iterative DFS with white/gray/black coloring.
    # Edges collapse multi-edges to simple edges for cycle purposes.
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in workflows}

    def visit(start: str) -> None:
        # Iterative DFS; stack carries (node, neighbor_iter, path_so_far).
        # path_so_far lets us emit the actual cycle when we hit a GRAY node.
        stack: list[tuple[str, list[str], list[str]]] = []
        outgoing = sorted({t for _sid, t in edges.get(start, []) if t in workflows})
        color[start] = GRAY
        stack.append((start, outgoing, [start]))
        while stack:
            node, remaining, path = stack[-1]
            if not remaining:
                color[node] = BLACK
                stack.pop()
                continue
            nxt = remaining.pop(0)
            if color[nxt] == GRAY:
                # Cycle: nxt is somewhere in `path`. Slice from there to end + nxt.
                idx = path.index(nxt)
                cycle_path = path[idx:] + [nxt]
                errors.append(
                    f"call cycle detected: {' -> '.join(cycle_path)} "
                    f"(code: call_cycle_detected)"
                )
                # Continue without recursing into nxt — one cycle report per entry-point edge.
            elif color[nxt] == WHITE:
                color[nxt] = GRAY
                nxt_outgoing = sorted({t for _sid, t in edges.get(nxt, []) if t in workflows})
                stack.append((nxt, nxt_outgoing, path + [nxt]))
            # BLACK = already fully explored; skip.

    for name in sorted(workflows):
        if color[name] == WHITE:
            visit(name)
    return errors


def validate_workflow(
    path: str, registry: "Registry | None" = None
) -> tuple[list[str], dict | None]:
    """Validate a workflow YAML file. Returns (errors, parsed_doc). Empty errors = valid.

    Optional ``registry``: if provided and the workflow contains
    ``action: mcp_tool_call`` steps, each step's ``server`` must be present in
    the registry or load fails. If not provided and the workflow has any
    ``mcp_tool_call`` steps, load fails with a registry-required message.
    Workflows with no such steps pass regardless (back-compat).
    """
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return [str(e)], None
    if size > YAML_MAX:
        raise RuntimeError(
            f"Workflow YAML '{path}' is {size} bytes, exceeds YAML_MAX of {YAML_MAX} bytes"
        )
    try:
        raw = Path(path).read_text()
    except (OSError, FileNotFoundError) as e:
        return [str(e)], None
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return [f"YAML parse error: {e}"], None
    errors = []
    if not isinstance(doc, dict):
        return [f"Workflow YAML must be a mapping, got {type(doc).__name__}"], None
    # schema_version is optional; default to "0.4" when omitted. No value rejection — YAGNI.
    if "schema_version" in doc:
        if not isinstance(doc["schema_version"], str):
            errors.append("schema_version must be a string")
    else:
        doc["schema_version"] = "0.4"
    if "conversation_repair" in doc:
        repair = doc["conversation_repair"]
        if not isinstance(repair, dict):
            errors.append("conversation_repair must be a mapping")
        else:
            repair_keys = {"on_go_back", "on_cancel", "on_digression", "on_clarification"}
            for k, v in repair.items():
                if k not in repair_keys:
                    errors.append(f"conversation_repair has unknown key '{k}'; valid keys: {sorted(repair_keys)}")
                elif not isinstance(v, str):
                    errors.append(f"conversation_repair['{k}'] must be a string")
    for key in ("name", "description", "category", "output_format"):
        if key not in doc:
            errors.append(f"Workflow missing required key: '{key}'")
    if "steps" not in doc or not isinstance(doc.get("steps"), list):
        errors.append("Workflow missing required key: 'steps' (must be a list)")
        return errors, None
    if len(doc["steps"]) == 0:
        errors.append("Workflow must have at least one step")
        return errors, None
    mcp_steps: list[tuple[str, dict]] = []
    for i, step in enumerate(doc["steps"]):
        if not isinstance(step, dict):
            errors.append(f"Step {i} must be a mapping")
            continue
        label = step.get("id", str(i))
        if step.get("action") == "mcp_tool_call":
            missing = _REQUIRED_STEP_KEYS_MCP - step.keys()
            if missing:
                errors.append(
                    f"Step {i} ('{step.get('id', '?')}') (mcp_tool_call) "
                    f"missing keys: {sorted(missing)}"
                )
            _validate_mcp_tool_call_step(step, label, errors)
            mcp_steps.append((label, step))
            continue
        missing = REQUIRED_STEP_KEYS - step.keys()
        if missing:
            errors.append(f"Step {i} ('{step.get('id', '?')}') missing keys: {sorted(missing)}")
        if "gates" in step and not isinstance(step["gates"], list):
            errors.append(f"Step '{step.get('id', i)}' gates must be a list")
        if "anti_patterns" in step and not isinstance(step["anti_patterns"], list):
            errors.append(f"Step '{step.get('id', i)}' anti_patterns must be a list")
        _validate_step_optional_fields(step, label, errors)
    # Registry cross-check for mcp_tool_call steps.
    if mcp_steps:
        wf_name = doc.get("name", Path(path).stem)
        if registry is None:
            errors.append(
                f"Workflow '{wf_name}' has {len(mcp_steps)} mcp_tool_call step(s) "
                f"but no registry was provided; pass --registry <path> or place "
                f"mcp_servers.yaml alongside the workflow "
                f"(code: mcp_tool_call_registry_required)"
            )
        else:
            available = registry.names()
            for step_label, step in mcp_steps:
                server = step.get("server")
                if not isinstance(server, str) or not server or "${" in server:
                    continue  # field-level error already recorded
                if server not in registry.servers:
                    errors.append(
                        f"Workflow '{wf_name}' step '{step_label}' references "
                        f"unknown MCP server {server!r}; available names: "
                        f"{available} (code: mcp_tool_call_unknown_server)"
                    )
    # Cross-step pass: precondition forward-refs, first-step, sub-path vs schemaless
    _validate_workflow_preconditions(doc["steps"], errors)
    # Cross-reference: inject_context 'from' must point to an existing step ID
    all_step_ids = {s.get("id") for s in doc["steps"] if isinstance(s, dict)}
    for step in doc["steps"]:
        if not isinstance(step, dict) or "inject_context" not in step:
            continue
        label = step.get("id", "?")
        for entry in step["inject_context"]:
            if isinstance(entry, dict) and "from" in entry and isinstance(entry["from"], str):
                if entry["from"] not in all_step_ids:
                    errors.append(f"Step '{label}' inject_context references nonexistent step '{entry['from']}'")
    # Cross-reference: branches[].next and default_branch must point to existing step IDs
    for step in doc["steps"]:
        if not isinstance(step, dict):
            continue
        label = step.get("id", "?")
        if "branches" in step and isinstance(step["branches"], list):
            for branch in step["branches"]:
                if isinstance(branch, dict) and isinstance(branch.get("next"), str):
                    if branch["next"] not in all_step_ids:
                        errors.append(f"Step '{label}' branch references nonexistent step '{branch['next']}'")
        if "default_branch" in step and isinstance(step["default_branch"], str):
            if step["default_branch"] not in all_step_ids:
                errors.append(f"Step '{label}' default_branch references nonexistent step '{step['default_branch']}'")
    # Validate top-level guardrails
    if "guardrails" in doc:
        guardrails = doc["guardrails"]
        if not isinstance(guardrails, list):
            errors.append("Workflow 'guardrails' must be a list")
        else:
            _GUARDRAIL_ACTIONS = {"warn", "force_branch", "escalate"}
            _TRIGGER_TYPES = {"keyword_match", "step_count", "step_revisit", "output_length"}
            for gi, gr in enumerate(guardrails):
                if not isinstance(gr, dict):
                    errors.append(f"guardrails[{gi}] must be a mapping")
                    continue
                for rk in ("id", "trigger", "action", "message"):
                    if rk not in gr:
                        errors.append(f"guardrails[{gi}] missing required key '{rk}'")
                if "action" in gr and gr["action"] not in _GUARDRAIL_ACTIONS:
                    errors.append(f"guardrails[{gi}] action must be one of {sorted(_GUARDRAIL_ACTIONS)}")
                if "trigger" in gr:
                    trigger = gr["trigger"]
                    if not isinstance(trigger, dict):
                        errors.append(f"guardrails[{gi}] trigger must be a mapping")
                    elif "type" not in trigger:
                        errors.append(f"guardrails[{gi}] trigger missing 'type'")
                    elif trigger["type"] not in _TRIGGER_TYPES:
                        errors.append(f"guardrails[{gi}] trigger type must be one of {sorted(_TRIGGER_TYPES)}")
                if gr.get("action") == "force_branch":
                    if "target_step" not in gr:
                        errors.append(f"guardrails[{gi}] force_branch action requires 'target_step'")
                    elif isinstance(gr["target_step"], str) and gr["target_step"] not in all_step_ids:
                        errors.append(f"guardrails[{gi}] target_step references nonexistent step '{gr['target_step']}'")

    return errors, doc


def load_workflow(path: str, registry: "Registry | None" = None) -> dict:
    """Load and validate a workflow YAML file. Returns plain dict."""
    errors, doc = validate_workflow(path, registry=registry)
    if errors:
        raise ValueError(errors[0])
    assert doc is not None
    return doc

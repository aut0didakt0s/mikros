"""YAML workflow schema parsing."""

import os
import re

import jsonschema
import yaml
from pathlib import Path

from .errors import YAML_MAX


REQUIRED_STEP_KEYS = {"id", "title", "directive_template", "gates", "anti_patterns"}

_REF_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


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
        # (d) sub-path against a step lacking output_schema and collect: true
        if len(parts) > 2:
            ref_step = sid_to_step[ref_sid]
            has_schema = "output_schema" in ref_step
            is_collect = ref_step.get("collect") is True
            if not has_schema and not is_collect:
                errors.append(
                    f"Step '{label}' precondition sub-path references step '{ref_sid}' "
                    f"which lacks both 'output_schema' and 'collect: true'; "
                    f"sub-path refs require one of these"
                )


def validate_workflow(path: str) -> tuple[list[str], dict | None]:
    """Validate a workflow YAML file. Returns (errors, parsed_doc). Empty errors = valid."""
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
    for i, step in enumerate(doc["steps"]):
        if not isinstance(step, dict):
            errors.append(f"Step {i} must be a mapping")
            continue
        missing = REQUIRED_STEP_KEYS - step.keys()
        if missing:
            errors.append(f"Step {i} ('{step.get('id', '?')}') missing keys: {sorted(missing)}")
        if "gates" in step and not isinstance(step["gates"], list):
            errors.append(f"Step '{step.get('id', i)}' gates must be a list")
        if "anti_patterns" in step and not isinstance(step["anti_patterns"], list):
            errors.append(f"Step '{step.get('id', i)}' anti_patterns must be a list")
        label = step.get("id", str(i))
        _validate_step_optional_fields(step, label, errors)
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


def load_workflow(path: str) -> dict:
    """Load and validate a workflow YAML file. Returns plain dict."""
    errors, doc = validate_workflow(path)
    if errors:
        raise ValueError(errors[0])
    assert doc is not None
    return doc

#!/usr/bin/env python3
"""megálos state machine — gate checks, task advancement, summary writing."""

import os
import re
import sys
import tempfile
from pathlib import Path

MEGALOS_DIR = Path(".megalos")
STATE_PATH = MEGALOS_DIR / "STATE.md"
DECISIONS_PATH = MEGALOS_DIR / "DECISIONS.md"


def atomic_write(path, content):
    """Write content to path atomically via temp file + rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        os.rename(tmp, str(path))
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_state():
    """Parse STATE.md into (dict, raw_text). Returns ({}, "") if missing."""
    if not STATE_PATH.exists():
        return {}, ""
    raw = STATE_PATH.read_text()
    state = {}
    for line in raw.split("\n"):
        if line.startswith("## "):
            break
        m = re.match(r"^(\w+):\s*(.*?)\s*$", line)
        if m:
            state[m.group(1)] = m.group(2)
    return state, raw


def rebuild_state(state, completed_lines, notes_section):
    lines = ["# megalos state", ""]
    for key in ["active_milestone", "active_slice", "active_task",
                "active_worktree", "active_worktree_path", "loc_budget"]:
        lines.append(f"{key}: {state.get(key, '')}")
    lines.append("")
    lines.append("## Recently completed")
    lines.append("")
    if completed_lines:
        for cl in completed_lines:
            lines.append(cl)
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(notes_section)
    lines.append("")
    return "\n".join(lines)


def parse_completed(raw):
    lines = []
    in_completed = False
    for line in raw.split("\n"):
        if line.strip() == "## Recently completed":
            in_completed = True
            continue
        if in_completed and line.startswith("## "):
            break
        if in_completed and line.strip().startswith("- "):
            lines.append(line)
    return lines


def parse_notes(raw):
    in_notes = False
    note_lines = []
    for line in raw.split("\n"):
        if line.strip() == "## Notes":
            in_notes = True
            continue
        if in_notes and line.startswith("## "):
            break
        if in_notes:
            note_lines.append(line)
    # Strip leading/trailing blank lines
    text = "\n".join(note_lines).strip()
    return text if text else ("This file is the single source of truth for "
                              '"where am I in the workflow?" Every megalos '
                              "command reads it first and writes it last "
                              "(atomically, via temp file + mv).")


# --- gate subcommand ---

def cmd_gate(args):
    if not args:
        print("Usage: megalos.py gate <command> [args...]", file=sys.stderr)
        return 1

    command = args[0]
    state, _ = parse_state()

    if command == "discuss":
        return 0

    if command == "plan-slice":
        milestone = state.get("active_milestone", "")
        if not milestone:
            print("Gate failed: no active milestone set in STATE.md",
                  file=sys.stderr)
            return 1
        context = MEGALOS_DIR / "plans" / milestone / "CONTEXT.md"
        if not context.exists():
            print(f"Gate failed: {context} does not exist", file=sys.stderr)
            return 1
        return 0

    if command == "execute-task":
        if len(args) < 2:
            print("Usage: megalos.py gate execute-task <task-id>",
                  file=sys.stderr)
            return 1
        task_id = args[1]
        for field in ["active_milestone", "active_slice", "active_task"]:
            if not state.get(field, ""):
                print(f"Gate failed: {field} not set in STATE.md",
                      file=sys.stderr)
                return 1
        if state["active_task"] != task_id:
            print(f"Gate failed: active_task is {state['active_task']}, "
                  f"not {task_id}", file=sys.stderr)
            return 1
        return 0

    if command in ("sniff-test", "compress"):
        milestone = state.get("active_milestone", "")
        slc = state.get("active_slice", "")
        if not milestone or not slc:
            print(f"Gate failed: active_milestone and active_slice must be "
                  f"set for {command}", file=sys.stderr)
            return 1
        slice_dir = MEGALOS_DIR / "plans" / milestone / slc
        if not slice_dir.exists():
            print(f"Gate failed: no completed tasks (directory {slice_dir} "
                  f"missing)", file=sys.stderr)
            return 1
        summaries = list(slice_dir.glob("T*-SUMMARY.md"))
        if not summaries:
            print(f"Gate failed: no T##-SUMMARY.md files in {slice_dir}",
                  file=sys.stderr)
            return 1
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 1


# --- advance subcommand ---

def cmd_advance(args):
    if not args:
        print("Usage: megalos.py advance <task-id>", file=sys.stderr)
        return 1

    task_id = args[0]
    state, raw = parse_state()
    milestone = state.get("active_milestone", "")
    slc = state.get("active_slice", "")

    if not milestone or not slc:
        print("Cannot advance: no active milestone/slice", file=sys.stderr)
        return 1

    plan_path = MEGALOS_DIR / "plans" / milestone / f"{slc}-PLAN.md"
    if not plan_path.exists():
        print(f"Cannot advance: {plan_path} not found", file=sys.stderr)
        return 1

    plan_text = plan_path.read_text()

    # Parse tasks and their LOC budgets from the plan
    tasks = []
    for m in re.finditer(
        r"### (T\d+)\s*[—–-]\s*(.+?)(?:\n|\r)",
        plan_text
    ):
        tid = m.group(1)
        title = m.group(2).strip()
        # Find LOC budget after this task header
        rest = plan_text[m.end():]
        bm = re.search(r"\*\*LOC budget:\*\*\s*(\d+)", rest)
        budget = int(bm.group(1)) if bm else 300
        tasks.append({"id": tid, "title": title, "budget": budget})

    # Find current task index
    current_idx = None
    for i, t in enumerate(tasks):
        if t["id"] == task_id:
            current_idx = i
            break

    if current_idx is None:
        print(f"Task {task_id} not found in {plan_path}", file=sys.stderr)
        return 1

    # Find current task title for completed line
    current_task = tasks[current_idx]
    completed = parse_completed(raw)
    completed.append(f"- {current_task['id']} — {current_task['title']} done")

    # Set next task or clear
    if current_idx + 1 < len(tasks):
        next_task = tasks[current_idx + 1]
        state["active_task"] = next_task["id"]
        state["loc_budget"] = str(next_task["budget"])
    else:
        state["active_task"] = ""
        state["loc_budget"] = ""

    # Clear worktree fields
    state["active_worktree"] = ""
    state["active_worktree_path"] = ""

    notes = parse_notes(raw)
    new_content = rebuild_state(state, completed, notes)
    atomic_write(STATE_PATH, new_content)
    return 0


# --- write-summary subcommand ---

def cmd_write_summary(args):
    if not args:
        print("Usage: megalos.py write-summary <task-id>", file=sys.stderr)
        return 1

    task_id = args[0]
    summary = sys.stdin.read()

    state, raw = parse_state()
    milestone = state.get("active_milestone", "")
    slc = state.get("active_slice", "")

    if not milestone or not slc:
        print("Cannot write summary: no active milestone/slice",
              file=sys.stderr)
        return 1

    # Write summary file atomically
    slice_dir = MEGALOS_DIR / "plans" / milestone / slc
    summary_path = slice_dir / f"{task_id}-SUMMARY.md"
    atomic_write(summary_path, summary)

    # Extract worktree info from summary
    branch_m = re.search(r"^- branch:\s*(.+)$", summary, re.MULTILINE)
    path_m = re.search(r"^- path:\s*(.+)$", summary, re.MULTILINE)
    if branch_m:
        state["active_worktree"] = branch_m.group(1).strip()
    if path_m:
        state["active_worktree_path"] = path_m.group(1).strip()

    notes = parse_notes(raw)
    completed = parse_completed(raw)
    new_state = rebuild_state(state, completed, notes)
    atomic_write(STATE_PATH, new_state)

    # Extract and append decisions
    decisions_section = extract_section(summary, "Decisions")
    if decisions_section and not is_empty_section(decisions_section):
        append_decisions(decisions_section)

    # Extract and append gotchas
    gotchas_section = extract_section(summary, "Gotchas")
    if gotchas_section and not is_empty_section(gotchas_section):
        append_gotchas(gotchas_section)

    return 0


def extract_section(text, heading):
    lines = text.split("\n")
    capture = False
    result = []
    for line in lines:
        if re.match(r"^###\s+", line) and heading.lower() in line.lower():
            capture = True
            continue
        if capture and re.match(r"^###\s+", line):
            break
        if capture:
            result.append(line)
    return "\n".join(result).strip()


def is_empty_section(text):
    cleaned = text.strip().strip("-").strip()
    return not cleaned or cleaned.lower() in ("(none)", "none", "n/a", "")


def append_decisions(content):
    if not DECISIONS_PATH.exists():
        return
    existing = DECISIONS_PATH.read_text()
    # Parse bullet points into decision entries
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("- ") and len(line) > 2:
            entry = line[2:].strip()
            if entry and not is_empty_section(entry):
                existing += f"\n## {entry}\n"
    atomic_write(DECISIONS_PATH, existing)


def append_gotchas(content):
    gotchas_path = Path(".claude/skills/simplicity-guard/references/gotchas.md")
    if not gotchas_path.exists():
        return
    existing = gotchas_path.read_text()
    existing += "\n" + content + "\n"
    atomic_write(gotchas_path, existing)


def main():
    if len(sys.argv) < 2:
        print("Usage: megalos.py <gate|advance|write-summary> [args...]",
              file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    rest = sys.argv[2:]

    if subcmd == "gate":
        sys.exit(cmd_gate(rest))
    elif subcmd == "advance":
        sys.exit(cmd_advance(rest))
    elif subcmd == "write-summary":
        sys.exit(cmd_write_summary(rest))
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

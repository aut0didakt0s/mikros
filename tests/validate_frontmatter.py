#!/usr/bin/env python3
"""
Validate a markdown file's YAML frontmatter.

Usage:
  validate_frontmatter.py <file> <required-key> [<required-key> ...]

Exits 0 if the file has a YAML frontmatter block and contains all required
keys. Exits 1 otherwise.

No external dependencies — uses only the stdlib. Parses frontmatter with a
simple line-based tokenizer rather than pulling in PyYAML.
"""
import sys
import re


def extract_frontmatter(text: str) -> dict:
    """Return a dict of top-level scalar key: value pairs from a frontmatter block."""
    if not text.startswith("---\n"):
        return {}
    # Find the closing ---
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    block = text[4:end]
    result = {}
    for line in block.splitlines():
        # Match simple "key: value" pairs. Skip list items, nested keys, blank lines.
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def main():
    if len(sys.argv) < 3:
        print("usage: validate_frontmatter.py <file> <required-key> [...]", file=sys.stderr)
        sys.exit(1)
    path = sys.argv[1]
    required = sys.argv[2:]
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"validate_frontmatter: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    fm = extract_frontmatter(text)
    if not fm:
        print(f"validate_frontmatter: no frontmatter block in {path}", file=sys.stderr)
        sys.exit(1)
    missing = [k for k in required if k not in fm]
    if missing:
        print(f"validate_frontmatter: missing keys in {path}: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

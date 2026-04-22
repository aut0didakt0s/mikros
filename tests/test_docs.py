from pathlib import Path

DOC_PATH = Path(__file__).parent.parent / "docs" / "VISUALIZATION.md"


def test_visualization_doc_exists_and_covers_required_topics() -> None:
    assert DOC_PATH.is_file(), f"{DOC_PATH} must exist after M009 ships"
    body = DOC_PATH.read_text()
    # Minimum substring coverage — keeps the test cheap and avoids
    # lint-style regressions while ensuring the three must-have
    # elements from the plan are present.
    for required in ("python -m megalos_server.diagram", "```mermaid", "flowchart TD"):
        assert required in body, f"docs/VISUALIZATION.md missing {required!r}"

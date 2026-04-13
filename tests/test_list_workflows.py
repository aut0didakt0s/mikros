"""Tests for list_workflows tool."""

from tests.conftest import call_tool


class TestListWorkflows:
    def test_list_all(self):
        r = call_tool("list_workflows", {})
        assert r["total"] == 5
        names = {w["name"] for w in r["workflows"]}
        assert names == {"coding", "essay", "blog", "research", "decision"}

    def test_filter_by_category(self):
        r = call_tool("list_workflows", {"category": "writing_communication"})
        names = {w["name"] for w in r["workflows"]}
        assert names == {"essay", "blog"}
        assert r["total"] == 2

    def test_filter_analysis(self):
        r = call_tool("list_workflows", {"category": "analysis_decision"})
        names = {w["name"] for w in r["workflows"]}
        assert names == {"research", "decision"}

    def test_filter_professional(self):
        r = call_tool("list_workflows", {"category": "professional"})
        names = {w["name"] for w in r["workflows"]}
        assert names == {"coding"}

    def test_filter_unknown_category(self):
        r = call_tool("list_workflows", {"category": "nonexistent"})
        assert r["workflows"] == []
        assert r["total"] == 0

    def test_each_workflow_has_category(self):
        r = call_tool("list_workflows", {})
        for wf in r["workflows"]:
            assert wf["category"], f"{wf['name']} missing category"
            assert wf["steps"] > 0
            assert wf["description"]

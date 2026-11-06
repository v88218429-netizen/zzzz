"""Unit checks for the read-only GitHub Actions audit."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "codex_ci_audit.py"
SPEC = importlib.util.spec_from_file_location("codex_ci_audit", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class WorkflowAuditTest(unittest.TestCase):
    def test_detects_workflows_and_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "unfiltered.yml").write_text(
                "name: CI\non:\n  pull_request:\n  workflow_dispatch:\n"
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - run: pip install pytest\n", encoding="utf-8"
            )
            (wf / "filtered.yaml").write_text(
                "name: CI2\non:\n  pull_request:\n    paths:\n"
                "      - 'src/**'\nconcurrency:\n  group: test\n"
                "jobs:\n  run:\n    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n          cache: pip\n"
                "      - run: ollama pull qwen2.5:1.5b\n", encoding="utf-8"
            )
            report = module.scan_workflows(root)
            self.assertEqual(report["workflow_count"], 2)
            self.assertEqual(report["event_counts"]["pull_request"], 2)
            self.assertEqual(report["event_counts"]["workflow_dispatch"], 1)
            self.assertEqual(report["review_candidates"]["pull_requests_without_paths"], ["unfiltered.yml"])
            self.assertEqual(report["review_candidates"]["ollama_model_downloads"], ["filtered.yaml"])
            self.assertEqual(report["review_candidates"]["without_concurrency"], ["unfiltered.yml"])
            self.assertEqual(report["review_candidates"]["pip_install_without_declared_cache"], ["unfiltered.yml"])

    def test_missing_directory_has_empty_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            report = module.scan_workflows(Path(d))
            self.assertEqual(report["workflow_count"], 0)

    def test_schedule_not_treated_as_pr(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "schedule.yml").write_text(
                "on:\n  schedule:\n    - cron: '0 * * * *'\n", encoding="utf-8"
            )
            report = module.scan_workflows(root)
            self.assertEqual(report["event_counts"]["schedule"], 1)
            self.assertEqual(report["review_candidates"]["pull_requests_without_paths"], [])


if __name__ == "__main__":
    unittest.main()

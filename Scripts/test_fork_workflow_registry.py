#!/usr/bin/env python3

import datetime as dt
import unittest

from ForkWorkflowRegistry import retirement_candidates


class RetirementCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.before = dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc)

    def test_selects_only_old_active_unprotected_workflows(self):
        workflows = [
            self.workflow(1, "old.yml", "2026-08-14T00:00:00Z"),
            self.workflow(2, "protected.yml", "2026-08-14T00:00:00Z"),
            self.workflow(3, "new.yml", "2026-08-23T00:00:00Z"),
            self.workflow(4, "disabled.yml", "2026-08-14T00:00:00Z", "disabled_manually"),
        ]

        result = retirement_candidates(
            workflows,
            {".github/workflows/protected.yml"},
            self.before,
        )

        self.assertEqual([1], [workflow["id"] for workflow in result])

    def test_cutoff_is_exclusive(self):
        workflow = self.workflow(1, "boundary.yml", "2026-08-16T00:00:00Z")

        self.assertEqual([], retirement_candidates([workflow], set(), self.before))

    def test_results_are_stable(self):
        workflows = [
            self.workflow(2, "z.yml", "2026-08-14T00:00:00Z"),
            self.workflow(1, "a.yml", "2026-08-14T00:00:00Z"),
        ]

        result = retirement_candidates(workflows, set(), self.before)

        self.assertEqual([1, 2], [workflow["id"] for workflow in result])

    @staticmethod
    def workflow(workflow_id, filename, created_at, state="active"):
        return {
            "id": workflow_id,
            "name": filename,
            "path": f".github/workflows/{filename}",
            "created_at": created_at,
            "state": state,
        }


if __name__ == "__main__":
    unittest.main()

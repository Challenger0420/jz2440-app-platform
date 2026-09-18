import json
import unittest

from apps.runboard.collector.matrix_metadata import (
    INFERRED,
    RELIABLE,
    UNKNOWN,
    matrix_root_from_cell,
    parse_matrix_observations,
)


class MatrixMetadataTests(unittest.TestCase):
    def test_cell_path_maps_to_matrix_root(self):
        self.assertEqual(
            matrix_root_from_cell("/private/run/formal/cells/cell_008"),
            "/private/run/formal",
        )
        self.assertIsNone(matrix_root_from_cell("/private/run/formal/cell_008"))

    def test_plan_and_completion_manifests_are_joined_without_guessing(self):
        plan = {
            "matrix_version": "prompt-horizontal-formal-45-v1",
            "cells": [
                {"index": 7, "run_id": "cell_007", "method_name": "Baseline"},
                {"index": 8, "run_id": "cell_008", "method_name": "PromptFL-style-CD"},
            ],
        }
        completed = {"cell_index": 7, "status": "completed_prompt_horizontal_formal_cell"}
        text = "\n".join((
            "__RUNBOARD_MATRIX_PLAN__ 123",
            json.dumps(plan),
            "__RUNBOARD_MATRIX_COMPLETION__ 123",
            json.dumps(completed),
        ))
        parsed = parse_matrix_observations(
            text, {123: "cell_008"}, {"prompt-horizontal-formal-45-v1": "Prompt Horizontal Formal V6"}
        )
        metadata = parsed[123]
        self.assertEqual(metadata.job_name, "Prompt Horizontal Formal V6")
        self.assertEqual(metadata.matrix_completed, 1)
        self.assertEqual(metadata.matrix_total, 2)
        self.assertEqual(metadata.current_cell, 8)
        self.assertEqual(metadata.method, "PromptFL-style-CD")
        self.assertEqual(metadata.field_sources["job_name"], RELIABLE)
        self.assertEqual(metadata.field_sources["current_cell"], RELIABLE)
        self.assertEqual(metadata.field_sources["matrix_completed"], RELIABLE)

    def test_missing_job_name_uses_safe_version_fallback_and_unknown_join(self):
        plan = {"matrix_version": "formal/v6 unsafe", "cells": []}
        text = "__RUNBOARD_MATRIX_PLAN__ 4\n" + json.dumps(plan)
        metadata = parse_matrix_observations(text, {4: "missing"})[4]
        self.assertEqual(metadata.job_name, "formal-v6-unsafe")
        self.assertEqual(metadata.field_sources["job_name"], INFERRED)
        self.assertIsNone(metadata.current_cell)
        self.assertEqual(metadata.field_sources["current_cell"], UNKNOWN)
        self.assertEqual(metadata.field_sources["matrix_completed"], UNKNOWN)

    def test_active_shard_selects_matching_parent_plan_and_manifests(self):
        shard = {
            "matrix_version": "prompt-horizontal-formal-45-v1",
            "parent_matrix_version": "prompt-horizontal-formal-45-v1",
            "parent_cell_indices": list(range(31, 46)),
            "cells": [
                {"index": 31, "run_id": "cell_031", "method_name": "PromptFL-style-CD"},
                {"index": 32, "run_id": "cell_032", "method_name": "PromptFL-style-CD"},
            ],
        }
        parent = {
            "matrix_version": "prompt-horizontal-formal-45-v1",
            "cells": [
                {"index": index, "run_id": "cell_{:03d}".format(index), "method_name": "PromptFL-style-CD"}
                for index in range(1, 46)
            ],
        }
        completion_lines = "\n".join(
            "__RUNBOARD_MATRIX_COMPLETION__ 123 parent\n" + json.dumps({"cell_index": index, "status": "completed"})
            for index in range(1, 10)
        )
        text = "\n".join((
            "__RUNBOARD_MATRIX_PLAN__ 123 shard",
            json.dumps(shard),
            "__RUNBOARD_MATRIX_COMPLETION__ 123 shard",
            json.dumps({"cell_index": 31, "status": "completed"}),
            "__RUNBOARD_MATRIX_PLAN__ 123 parent",
            json.dumps(parent),
            completion_lines,
        ))
        metadata = parse_matrix_observations(
            text,
            {123: "cell_031"},
            {"prompt-horizontal-formal-45-v1": "Prompt Horizontal Formal V6"},
        )[123]
        self.assertEqual(metadata.job_name, "Prompt Horizontal Formal V6")
        self.assertEqual(metadata.matrix_total, 45)
        self.assertEqual(metadata.current_cell, 31)
        self.assertEqual(metadata.matrix_completed, 10)
        self.assertEqual(metadata.method, "PromptFL-style-CD")


if __name__ == "__main__":
    unittest.main()

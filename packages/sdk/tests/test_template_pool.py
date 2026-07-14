import json
import unittest
from importlib import resources


POOL_PACKAGE = "data_intelligence_sdk.templates.pool"


class BuiltinTemplatePoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = resources.files(POOL_PACKAGE)
        self.manifest = json.loads(
            self.pool.joinpath("manifest.json").read_text(encoding="utf-8")
        )

    def test_manifest_lists_three_unique_templates(self) -> None:
        entries = self.manifest["templates"]

        self.assertEqual(len(entries), 3)
        self.assertEqual(
            {entry["template_id"] for entry in entries},
            {
                "executive-overview",
                "time-series-analysis",
                "segment-comparison",
            },
        )
        self.assertEqual(
            len({(entry["template_id"], entry["version"]) for entry in entries}),
            len(entries),
        )

    def test_manifest_resources_exist_and_parse(self) -> None:
        schema_resource = self.pool.joinpath(self.manifest["template_schema"])
        schema = json.loads(schema_resource.read_text(encoding="utf-8"))

        self.assertTrue(schema_resource.is_file())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

        for entry in self.manifest["templates"]:
            with self.subTest(template_id=entry["template_id"]):
                resource = self.pool.joinpath(entry["path"])
                payload = json.loads(resource.read_text(encoding="utf-8"))
                self.assertTrue(resource.is_file())
                self.assertEqual(payload["template_id"], entry["template_id"])
                self.assertEqual(payload["version"], entry["version"])
                self.assertEqual(payload["schema_version"], "1.0")

    def test_template_cross_references_are_consistent(self) -> None:
        for entry in self.manifest["templates"]:
            payload = json.loads(
                self.pool.joinpath(entry["path"]).read_text(encoding="utf-8")
            )
            requirement_ids = {
                requirement["requirement_id"]
                for requirement in payload["data_requirements"]
            }
            section_ids = [section["section_id"] for section in payload["sections"]]
            block_ids = []
            chart_slot_ids = []

            self.assertEqual(
                len(requirement_ids),
                len(payload["data_requirements"]),
                f"Duplicate requirement ID in {entry['template_id']}",
            )
            self.assertEqual(
                len(section_ids),
                len(set(section_ids)),
                f"Duplicate section ID in {entry['template_id']}",
            )

            for section in payload["sections"]:
                for block in section["blocks"]:
                    block_ids.append(block["block_id"])
                    self.assertTrue(
                        set(block["data_requirement_refs"]).issubset(requirement_ids)
                    )
                    chart_slot = block.get("chart_slot")
                    if chart_slot is not None:
                        chart_slot_ids.append(chart_slot["chart_slot_id"])
                        self.assertEqual(block["type"], "chart")
                        self.assertTrue(
                            set(chart_slot["data_requirement_refs"]).issubset(
                                requirement_ids
                            )
                        )

            self.assertEqual(
                len(block_ids),
                len(set(block_ids)),
                f"Duplicate block ID in {entry['template_id']}",
            )
            self.assertEqual(
                len(chart_slot_ids),
                len(set(chart_slot_ids)),
                f"Duplicate chart slot ID in {entry['template_id']}",
            )


if __name__ == "__main__":
    unittest.main()

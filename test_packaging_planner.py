import copy
import unittest

from packaging_planner import build_item, model_uses_lite_body, regenerate_packaging


class RegeneratePackagingTests(unittest.TestCase):
    def setUp(self):
        self.items = [{
            "id": "item-tables",
            "source_file": "invoice.csv",
            "description": "6ft Champion pool table",
            "size": "6ft",
            "model": "Champion",
            "colour": "Black",
            "quantity": 6,
            "item_type": "complete_table",
            "po_number": "PO123",
            "notes": "",
            "confidence": 1,
            "raw_text": "",
        }]

    @staticmethod
    def pallet(result, pallet_type):
        return next(
            pallet
            for pallet in result["pallets"]
            if pallet["pallet_type"] == pallet_type
        )

    def manual_layout(self):
        initial = regenerate_packaging(self.items)
        body_pallet = self.pallet(initial, "body")
        rail_pallet = self.pallet(initial, "top_rail")
        rail_line = rail_pallet["lines"][0]
        moved_line = copy.deepcopy(rail_line)
        moved_line["id"] = "line-manual-move"
        moved_line["quantity"] = 1
        rail_line["quantity"] -= 1
        body_pallet["carried_top_rails"].append(moved_line)
        body_pallet["manual_override"] = True
        rail_pallet["manual_override"] = True
        return initial["pallets"]

    def test_regenerate_preserves_manual_move(self):
        result = regenerate_packaging(
            self.items,
            existing_pallets=self.manual_layout(),
        )

        body_pallet = self.pallet(result, "body")
        rail_pallet = self.pallet(result, "top_rail")
        self.assertTrue(result["manual_layout_preserved"])
        self.assertEqual(1, sum(
            line["quantity"] for line in body_pallet["carried_top_rails"]
        ))
        self.assertEqual(5, sum(line["quantity"] for line in rail_pallet["lines"]))
        self.assertEqual(1, result["summary"]["carried_top_rails"])
        self.assertNotIn(
            "top_rail_mismatch",
            {warning["code"] for warning in result["warnings"]},
        )

        second_result = regenerate_packaging(
            self.items,
            existing_pallets=result["pallets"],
        )
        self.assertEqual(1, second_result["summary"]["carried_top_rails"])
        self.assertEqual(
            5,
            sum(
                line["quantity"]
                for line in self.pallet(second_result, "top_rail")["lines"]
            ),
        )

    def test_regenerate_preserves_single_body_move(self):
        initial = regenerate_packaging(self.items)
        body_pallets = [
            pallet
            for pallet in initial["pallets"]
            if pallet["pallet_type"] == "body"
        ]
        self.assertEqual([5, 1], [
            sum(line["quantity"] for line in pallet["lines"])
            for pallet in body_pallets
        ])

        source_line = body_pallets[0]["lines"][0]
        moved_line = copy.deepcopy(source_line)
        moved_line["id"] = "line-single-body-move"
        moved_line["quantity"] = 1
        source_line["quantity"] -= 1
        body_pallets[1]["lines"].append(moved_line)
        body_pallets[0]["manual_override"] = True
        body_pallets[1]["manual_override"] = True

        result = regenerate_packaging(
            self.items,
            existing_pallets=initial["pallets"],
        )
        regenerated_body_pallets = [
            pallet
            for pallet in result["pallets"]
            if pallet["pallet_type"] == "body"
        ]
        self.assertTrue(result["manual_layout_preserved"])
        self.assertEqual([4, 2], [
            sum(line["quantity"] for line in pallet["lines"])
            for pallet in regenerated_body_pallets
        ])
        self.assertNotIn(
            "body_mismatch",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_regenerate_updates_items_while_replaying_move(self):
        manual_layout = self.manual_layout()
        updated_items = copy.deepcopy(self.items)
        updated_items[0]["colour"] = "Stone"
        updated_items[0]["quantity"] = 4

        result = regenerate_packaging(
            updated_items,
            existing_pallets=manual_layout,
            baseline_items=self.items,
        )

        body_pallet = self.pallet(result, "body")
        rail_pallet = self.pallet(result, "top_rail")
        self.assertEqual(1, sum(
            line["quantity"] for line in body_pallet["carried_top_rails"]
        ))
        self.assertEqual(3, sum(line["quantity"] for line in rail_pallet["lines"]))
        rail_lines = body_pallet["carried_top_rails"] + rail_pallet["lines"]
        self.assertEqual({"Stone"}, {line["colour"] for line in rail_lines})
        self.assertEqual(
            len(result["pallets"]),
            len({pallet["pallet_number"] for pallet in result["pallets"]}),
        )
        self.assertNotIn(
            "empty_pallet",
            {warning["code"] for warning in result["warnings"]},
        )
        self.assertNotIn(
            "top_rail_mismatch",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_regenerate_updates_config_while_replaying_move(self):
        automatic = regenerate_packaging(self.items)
        updated_config = {**automatic["config"], "body_capacity": 3}

        result = regenerate_packaging(
            self.items,
            config=updated_config,
            existing_pallets=self.manual_layout(),
            baseline_items=self.items,
            baseline_config=automatic["config"],
        )

        body_pallets = [
            pallet
            for pallet in result["pallets"]
            if pallet["pallet_type"] == "body"
        ]
        self.assertEqual([3, 3], [
            sum(line["quantity"] for line in pallet["lines"])
            for pallet in body_pallets
        ])
        self.assertEqual(1, result["summary"]["carried_top_rails"])
        self.assertNotIn(
            "body_capacity",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_explicit_reset_discards_manual_move(self):
        result = regenerate_packaging(
            self.items,
            existing_pallets=self.manual_layout(),
            replace_manual_layout=True,
        )

        self.assertFalse(result["manual_layout_preserved"])
        self.assertEqual(0, result["summary"]["carried_top_rails"])
        self.assertEqual(6, sum(
            line["quantity"] for line in self.pallet(result, "top_rail")["lines"]
        ))

    def test_automatic_layout_regenerates_without_manual_changes(self):
        initial = regenerate_packaging(self.items)
        result = regenerate_packaging(
            self.items,
            existing_pallets=initial["pallets"],
        )

        self.assertFalse(result["manual_layout_preserved"])
        self.assertEqual(0, result["summary"]["carried_top_rails"])

    def test_league_and_lite_models_use_lite_body_stock(self):
        self.assertTrue(model_uses_lite_body("League"))
        self.assertTrue(model_uses_lite_body("Lite"))
        self.assertFalse(model_uses_lite_body("Champion"))
        self.assertEqual(
            "Lite",
            build_item({"description": "7ft Lite Pool table - Black"})["model"],
        )


if __name__ == "__main__":
    unittest.main()

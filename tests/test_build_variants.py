import ast
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/build_dashboard.py"


def load_detector():
    tree = ast.parse(SOURCE.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "detect_variant_conflicts")
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {"pd": pd}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["detect_variant_conflicts"]


class BuildVariantDetectionTest(unittest.TestCase):
    def test_detects_conflict_but_ignores_identical_objective_rows(self):
        detect = load_detector()
        data = pd.DataFrame(
            [
                ("PU A", "SDL A", "123", "EWR1", "Policy A", "Climate A", "Obj1", "Baseflow", 0.52),
                ("PU A", "SDL A", "123", "EWR1", "Policy A", "Climate A", "Obj2", "Fresh", 0.52),
                ("PU A", "SDL A", "123", "EWR1", "Policy A", "Climate A", "Obj3", "Fresh", 0.66),
            ],
            columns=[
                "PlanningUnitName",
                "SWSDLName",
                "Gauge",
                "EWR",
                "Policy",
                "ClimateModel",
                "EcoObj",
                "FlowRegime",
                "FR",
            ],
        )

        conflicts = detect(
            data,
            ["PlanningUnitName", "SWSDLName", "Gauge", "EWR", "Policy", "ClimateModel"],
            "FR",
        )

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["values"], [0.52, 0.66])

        data["FR"] = 0.52
        self.assertEqual(
            detect(
                data,
                ["PlanningUnitName", "SWSDLName", "Gauge", "EWR", "Policy", "ClimateModel"],
                "FR",
            ),
            [],
        )

    def test_frequency_ratio_data_has_301_conflict_groups(self):
        detect = load_detector()
        data = pd.read_csv(
            ROOT / "data/results/frequency_ratio.csv",
            dtype={"Gauge": str},
            usecols=[
                "PlanningUnitName",
                "SWSDLName",
                "Gauge",
                "EWR",
                "Policy",
                "ClimateModel",
                "FR",
            ],
        )
        data["Gauge"] = data.Gauge.replace({"A4260527": "A4260524"})

        conflicts = detect(
            data,
            ["PlanningUnitName", "SWSDLName", "Gauge", "EWR", "Policy", "ClimateModel"],
            "FR",
        )

        self.assertEqual(len(conflicts), 301)


if __name__ == "__main__":
    unittest.main()

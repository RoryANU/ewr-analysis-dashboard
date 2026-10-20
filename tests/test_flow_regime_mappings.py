import unittest
from pathlib import Path

import pandas as pd

from ewr_analysis.flow_regime_mappings import regimes_for

ROOT = Path(__file__).resolve().parents[1]

KNOWN_EXCEPTIONS = {("BF1", "VIC"), ("BF2", "VIC")}


class FlowRegimeMappingsTest(unittest.TestCase):
    def test_all_frequency_ratio_ewr_state_pairs_match_csv(self):
        data = pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype=str)
        mismatches = []
        observed_exceptions = set()
        for (code, state), rows in data.groupby(["EWR", "State"], dropna=False):
            expected = set(rows["FlowRegime"].dropna())
            actual = set(regimes_for(code, state))
            if actual != expected:
                pair = (code, state)
                if pair in KNOWN_EXCEPTIONS:
                    observed_exceptions.add(pair)
                    continue
                mismatches.append(f"{code!r}/{state!r}: mapped={sorted(actual)!r}, CSV={sorted(expected)!r}")

        # The CSV has gauge-dependent regimes: BF1 is Baseflow at gauges 405201/405202/405203/407205/407224
        # and Fresh at Wimmera gauges 415200/415203/415223/415246. BF2 is Baseflow at 407205/407224
        # and Fresh at Wimmera gauges 415200/415246. EWR+state alone cannot reproduce these distinctions.
        self.assertEqual(
            observed_exceptions,
            KNOWN_EXCEPTIONS,
            f"Known exceptions changed; review and update the exception set:\nexpected={sorted(KNOWN_EXCEPTIONS)!r}, observed={sorted(observed_exceptions)!r}",
        )
        self.assertEqual(
            mismatches,
            [],
            "Flow-regime mismatches against frequency_ratio.csv:\n" + "\n".join(mismatches),
        )

    def test_overlapping_codes_use_the_most_specific_mapping(self):
        self.assertEqual(regimes_for("LF1_S", "VIC"), ["Large fresh"])
        self.assertEqual(regimes_for("LF1_P", "VIC"), ["Large fresh"])
        self.assertEqual(regimes_for("SF-FD1", "QLD"), ["Baseflow"])


if __name__ == "__main__":
    unittest.main()

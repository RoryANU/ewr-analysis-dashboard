"""Hand-checked cases for flow_regime_mappings.regimes_for (the MDBA pipeline's EWR-code -> flow-regime rule, longest matching key wins)."""

from pathlib import Path

import pandas as pd
import pytest

from flow_regime_mappings import flow_mappings, regimes_for

ROOT = Path(__file__).resolve().parents[1]


class TestRegimesFor:
    @pytest.mark.parametrize(
        ("code", "state", "expected"),
        [
            ("BF1", "VIC", ["Baseflow"]),  # keys 'BF' (2) and 'F' (1) both occur in 'BF1': the longer one wins
            ("F1", "VIC", ["Fresh"]),
            ("SF1_P", "NSW", ["Small fresh"]),
            ("OB-L1", "NSW", ["Overbank large"]),  # beats the generic 'OB' (Overbank)
            ("OB-S2", "NSW", ["Overbank small"]),
            ("OB3", "NSW", ["Overbank"]),
            ("W-LF4", "NSW", ["Wetland Large Fresh"]),  # beats 'LF' (Large fresh)
            ("LF2", "NSW", ["Large fresh"]),
            ("BF-FD1", "QLD", ["Baseflow"]),  # 'BF-FD1' (6) beats 'FD1' (3), which maps to two regimes
            ("FD1", "QLD", ["Baseflow", "Cease to flow"]),
            ("FD2", "QLD", ["Cease to flow", "Fresh"]),
            ("BBR1_a", "QLD", ["Overbank", "Wetland inundation"]),
            ("FrL1", "QLD", ["excluded"]),
            ("CLLMM1c", "SA", ["lower lakes"]),
            ("CLLMM1a", "SA", ["Annual Barrage flow"]),
            ("IC2", "SA", ["Small fresh"]),
            ("BF1", "ACT", ["Baseflow"]),
            ("LF1_S", "VIC", ["Large fresh"]),  # 'LF' (2) beats 'F' (1)
            ("Wetland_1", "VIC", ["Wetland inundation"]),
        ],
    )
    def test_known_codes(self, code, state, expected):
        assert regimes_for(code, state) == expected

    @pytest.mark.parametrize(("code", "state"), [("ZZ9", "VIC"), ("", "VIC"), ("BF1", "WA"), ("BF1", ""), ("bf1", "VIC"), ("IC2", "NSW")])
    def test_no_match_gives_an_empty_list(self, code, state):
        assert regimes_for(code, state) == []

    def test_a_list_value_is_returned_as_a_copy(self):
        out = regimes_for("FD1", "QLD")
        out.append("mutated")
        assert regimes_for("FD1", "QLD") == ["Baseflow", "Cease to flow"]
        assert flow_mappings["QLD"]["FD1"] == ["Baseflow", "Cease to flow"]

    def test_a_string_value_is_wrapped_in_a_list(self):
        assert regimes_for("BF1", "ACT") == ["Baseflow"]


class TestMappingTable:
    def test_values_are_names_or_non_empty_name_lists(self):
        for state, table in flow_mappings.items():
            assert state and table
            for key, val in table.items():
                assert key and key == key.strip(), (state, key)
                names = val if isinstance(val, list) else [val]
                assert names and all(isinstance(n, str) and n.strip() for n in names), (state, key)

    def test_states_are_the_expected_set(self):
        assert set(flow_mappings) == {"QLD", "NSW", "VIC", "ACT", "SA"}

    def test_ties_between_equally_specific_keys_are_only_the_known_nsw_wetland_overbank_codes(self):
        """If two matching keys have the same length and different regimes the result depends on dict order (first listed wins, as in the
        upstream pipeline). Today that is only NSW 'OB-WL*' (keys 'OB' and 'WL'), which the results file also labels Overbank (checked in
        test_flow_regime_mappings); a new tie in the data would fail here."""
        d = pd.read_csv(ROOT / "data/results/frequency_ratio.csv", dtype=str, usecols=["EWR", "State"]).drop_duplicates()
        ties = set()
        for code, state in zip(d.EWR, d.State, strict=False):
            hits = [(k, str(v)) for k, v in flow_mappings.get(state, {}).items() if k in code]
            if not hits:
                continue
            best = max(len(k) for k, _ in hits)
            if len({v for k, v in hits if len(k) == best}) > 1:
                ties.add((code, state))
        assert ties == {("OB-WL", "NSW"), ("OB-WL1_S", "NSW"), ("OB-WL3_S", "NSW"), ("OB-WL4", "NSW"), ("OB-WL_S", "NSW")}
        assert all(regimes_for(code, state) == ["Overbank"] for code, state in ties)

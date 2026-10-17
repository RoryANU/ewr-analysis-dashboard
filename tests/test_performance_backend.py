"""Coverage metadata and diagnostic inventory must not conceal unsupported rules."""

import json

import server


def test_inventory_includes_level_rules_without_claiming_diagnostics():
    inventory = server.diagnostic_inventory("A4260524")
    assert inventory
    assert any(r["kind"] == "L" and not r["detailed"] for r in inventory)
    json.dumps(inventory, allow_nan=False)


def test_inventory_matches_supported_selector():
    gauge = "416001"
    supported = {(e["code"], pu) for e in server.ewr_list(gauge) for pu in e["pus"]}
    inventory = server.diagnostic_inventory(gauge)
    assert supported
    assert {(r["code"], r["pu"]) for r in inventory if r["detailed"]} == supported

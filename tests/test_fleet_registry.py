"""Offline registry tests: no broker calls or credentials."""
import json

import pytest

from obsidian_prediction.fleet_registry import RegistryError, load, plan, validate


def entry(**overrides):
    x = {"id": "parity_paper", "enabled": True,
         "module": "obsidian_prediction.parity_paper",
         "args": ["run", "--mode", "paper"]}
    x.update(overrides)
    return x


@pytest.mark.parametrize("items", [
    {}, [None], [entry(), entry()], [entry(enabled=1)],
    [entry(module="os")], [entry(module="bad;name")],
    [entry(args="run --mode paper")], [entry(args=["run", "--mode", "live"])],
    [entry(args=["run"])], [entry(args=["run", "--mode", "paper\n"])],
    [entry(id="../escape")], [entry(args=["run", "--mode", "paper", "\n"])],
])
def test_fail_closed_registry(items):
    with pytest.raises(RegistryError):
        validate(items)


def test_copy_workers_are_discovered_without_hardcoding():
    entries = validate([entry(), entry(
        id="bonereaper_copy", module="obsidian_prediction.copy_worker",
        args=["run", "--mode", "observe", "--leader", "bonereaper"]
    ), entry(id="future_disabled", enabled=False)])
    output = plan(entries)
    assert output["enabled"] == ["parity_paper", "bonereaper_copy"]
    assert output["disabled"] == ["future_disabled"]
    assert output["expected"] == 3
    assert output["alive"] == []  # registry membership != process liveness
    assert plan(entries, "bonereaper_copy")["expected"] == 1


def test_missing_optional_telegram_not_a_registry_failure():
    assert len(validate([entry()])) == 1


def test_invalid_selection_is_explicit():
    with pytest.raises(RegistryError, match="Unknown bot"):
        plan(validate([entry()]), "nonexistent")


def test_file_roundtrip(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps([entry()]), encoding="utf-8")
    assert load(path)[0].mode == "paper"

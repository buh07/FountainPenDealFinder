from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_eval_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "scripts" / "evaluate_baseline_models.py"
    spec = spec_from_file_location("evaluate_baseline_models", module_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_holdout_required_fails_gate_when_eval_is_training_fallback():
    mod = _load_eval_module()

    report = mod.build_report(
        resale_eval={"rows": 20, "mape": 0.1},
        auction_eval={"rows": 20, "mape": 0.1},
        min_rows=5,
        resale_max_mape=0.5,
        auction_max_mape=0.5,
        require_holdout=True,
        resale_eval_mode="all_rows_fallback_small_holdout",
        auction_eval_mode="holdout_test",
        resale_significance={"applicable": False, "pass": None},
        auction_significance={"applicable": False, "pass": None},
    )

    assert report["gates"]["resale_holdout_ok"] is False
    assert report["gates"]["resale_pass"] is False
    assert report["gates"]["overall_pass"] is False


def test_significance_gate_requires_candidate_to_be_statistically_better():
    mod = _load_eval_module()

    passing = mod._significance_from_errors(
        candidate_errors=[0.10] * 30,
        reference_errors=[0.30] * 30,
        alpha=0.05,
        bootstrap_samples=400,
    )
    failing = mod._significance_from_errors(
        candidate_errors=[0.32] * 30,
        reference_errors=[0.30] * 30,
        alpha=0.05,
        bootstrap_samples=400,
    )

    assert passing["applicable"] is True
    assert passing["pass"] is True
    assert failing["applicable"] is True
    assert failing["pass"] is False


def test_significance_failure_blocks_overall_gate():
    mod = _load_eval_module()

    report = mod.build_report(
        resale_eval={"rows": 20, "mape": 0.1},
        auction_eval={"rows": 20, "mape": 0.1},
        min_rows=5,
        resale_max_mape=0.5,
        auction_max_mape=0.5,
        require_holdout=True,
        resale_eval_mode="holdout_test",
        auction_eval_mode="holdout_test",
        resale_significance={"applicable": True, "pass": False},
        auction_significance={"applicable": False, "pass": None},
    )

    assert report["gates"]["resale_significance_pass"] is False
    assert report["gates"]["resale_pass"] is False
    assert report["gates"]["overall_pass"] is False

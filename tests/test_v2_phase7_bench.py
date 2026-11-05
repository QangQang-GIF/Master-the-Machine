"""V2 Phase 7: the replay harness is deterministic and honest about being a model."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("bench_v2_replay", Path(__file__).resolve().parents[1] / "scripts" / "bench_v2_replay.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_same_seed_gives_identical_results_and_different_seed_differs():
    assert bench.run(7, 50) == bench.run(7, 50)
    assert bench.run(7, 50) != bench.run(8, 50)


def test_output_is_labelled_as_model_not_measurement():
    out = bench.run(7, 20)
    assert "NOT measured" in out["model"]
    assert "Model only" in out["comparison"]["caveat"]


def test_pi_reserve_removes_pi_wait_in_the_model():
    out = bench.run(11, 100)
    assert out["v2"]["piWaitSeconds"]["p95"] == 0.0
    assert out["v2"]["piWaitSeconds"]["p95"] <= out["v1"]["piWaitSeconds"]["p95"]

import compare_strategies


def test_spread_reports_mean_sd_and_range():
    base = {"clips": 16, "model_calls": 51, "images_sent": 252, "input_tokens": 100, "output_tokens": 10,
            "recall": 0.5, "precision": 0.5, "mean_tiou": 0.7, "short_event_recall": 0.0,
            "no_event_clean_rate": 1.0, "scene_accuracy": 1.0, "fallback_clips": 0}
    runs = [dict(base, f1=v) for v in (0.486, 0.526, 0.541)]
    out = compare_strategies.spread(runs)
    assert out["runs"] == 3 and out["f1"] == 0.518
    assert out["f1_min"] == 0.486 and out["f1_max"] == 0.541
    assert 0.02 < out["f1_sd"] < 0.04
    assert out["model_calls"] == 51

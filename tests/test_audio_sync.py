import numpy as np

from covermaker.audio_sync import (
    detect_first_prominent_transient,
    estimate_correlation_lag,
    normalized_offsets,
)


def test_normalized_offsets_align_markers():
    assert np.allclose(normalized_offsets([1.0, 1.4, 0.8]), [0.4, 0.0, 0.6])


def test_detect_clap_like_transient():
    sr = 8000
    x = np.zeros(sr * 4, dtype=np.float32)
    x[int(1.2 * sr):int(1.2 * sr) + 20] = 1.0
    detection = detect_first_prominent_transient(x, sr)
    assert detection is not None
    assert abs(detection.time - 1.2) < 0.03
    assert detection.confidence >= 3.0


def test_correlation_lag_sign():
    sr = 8000
    rng = np.random.default_rng(123)
    base = np.zeros(sr * 5, dtype=np.float32)
    # Sparse, distinct onset pattern.
    for t in (0.8, 1.7, 2.6, 3.2):
        i = int(t * sr)
        base[i:i+120] += rng.normal(0, 1, 120).astype(np.float32)
    delayed = np.concatenate([np.zeros(int(0.35 * sr), dtype=np.float32), base])[: base.size]
    estimate = estimate_correlation_lag(base, delayed, sr, max_lag_seconds=1.0)
    assert estimate is not None
    lag, _ = estimate
    assert abs(lag + 0.35) <= 0.03

"""Cognitive scoring math for the memory engine.

Pure functions — no I/O, no side effects, no external dependencies beyond
Python stdlib ``math``.

ACT-R base-level activation (Anderson, 1993):
    B(M) = ln(n + 1) − d · ln(age / (n + 1))

Hebbian weight update (log-space, bidirectional):
    log_new = log(w) + log(1 + rate)

Ebbinghaus retention curve:
    R(t) = e^(−t / S)

Composite retrieval score:
    score = bm25 · softplus(act_r + 0.3 · hebbian) · confidence
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# ACT-R
# ---------------------------------------------------------------------------


def act_r_activation(
    access_count: int,
    age_days: float,
    decay: float = 0.5,
) -> float:
    """ACT-R base-level activation (Anderson, 1993).

    ``B(M) = ln(n + 1) − d · ln(age / (n + 1))``

    Parameters
    ----------
    access_count:
        Number of times the memory has been retrieved (*n*).
    age_days:
        Days since the memory was last accessed.
    decay:
        Decay exponent *d* (default 0.5, per Anderson 1993).

    Returns
    -------
    float
        Activation level (higher = more accessible).
    """
    n = access_count
    age = max(age_days, 0.001)  # floor to avoid log(0)
    return math.log(n + 1) - decay * math.log(age / (n + 1))


# ---------------------------------------------------------------------------
# Hebbian learning
# ---------------------------------------------------------------------------


def hebbian_update(
    current_weight: float,
    learning_rate: float = 0.1,
) -> float:
    """Hebbian weight update in log-space.

    ``log_new = log(w) + log(1 + rate)``

    Prevents weight explosion by operating in log-space.
    Should be called for both directions of a bidirectional edge.

    Parameters
    ----------
    current_weight:
        Current association weight ∈ (0, 1].
    learning_rate:
        Learning rate (default 0.1).

    Returns
    -------
    float
        Updated weight, clamped to [0, 1].
    """
    log_w = math.log(max(current_weight, 1e-10))
    log_new = log_w + math.log(1 + learning_rate)
    return min(math.exp(log_new), 1.0)


def hebbian_decay(
    weight: float,
    hours_since_last: float,
    half_life_hours: float = 168.0,
) -> float:
    """Exponential decay of an association weight.

    ``w_new = w · e^(−0.693 · Δt / t½)``

    Parameters
    ----------
    weight:
        Current association weight.
    hours_since_last:
        Hours since the association was last co-activated.
    half_life_hours:
        Half-life in hours (default 168 = 1 week).

    Returns
    -------
    float
        Decayed weight.
    """
    if half_life_hours <= 0:
        return 0.0
    return weight * math.exp(-0.693 * hours_since_last / half_life_hours)


# ---------------------------------------------------------------------------
# Ebbinghaus retention
# ---------------------------------------------------------------------------


def ebbinghaus_retention(
    elapsed_hours: float,
    access_count: int = 0,
    half_life_hours: float = 168.0,
    importance: float | None = None,
) -> float:
    """Ebbinghaus forgetting curve with access-count reinforcement.

    ``R(t) = e^(−t / S)``

    where ``S = half_life · (1 + ln(1 + n)) · importance_factor``.

    Parameters
    ----------
    elapsed_hours:
        Hours since last access.
    access_count:
        Number of prior retrievals.
    half_life_hours:
        Base half-life in hours (default 168 = 1 week).
    importance:
        Optional importance value ∈ [0, 1].  Maps to factor [0.5, 1.5].
        ``None`` → factor 1.0.

    Returns
    -------
    float
        Retention score ∈ [0, 1].
    """
    base_strength = half_life_hours * (1.0 + math.log(1.0 + access_count))

    if importance is not None:
        imp = max(0.0, min(1.0, importance))
        factor = 0.5 + imp
    else:
        factor = 1.0

    strength = base_strength * factor
    if strength <= 0:
        return 0.0

    return math.exp(-max(0.0, elapsed_hours) / strength)


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------


def composite_score(
    bm25_score: float,
    act_r_score: float,
    hebbian_boost: float,
    confidence: float,
) -> float:
    """Composite retrieval score combining content match with cognitive activation.

    ``score = bm25 · softplus(act_r + 0.3 · hebbian) · confidence``

    Parameters
    ----------
    bm25_score:
        BM25 full-text match score (from FTS5).
    act_r_score:
        ACT-R base-level activation.
    hebbian_boost:
        Summed Hebbian weight from associated engrams.
    confidence:
        Confidence ∈ [0, 1] of the memory.

    Returns
    -------
    float
        Final ranking score (higher = more relevant).
    """
    combined = act_r_score + 0.3 * hebbian_boost
    activated = math.log(1 + math.exp(combined))  # softplus
    return bm25_score * activated * confidence

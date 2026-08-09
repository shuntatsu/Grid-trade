from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from grid_trade.calibration.microstructure_contracts import OfiImpactSample, TopOfBookObservation
from grid_trade.domain.numeric import deterministic_decimal_context

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _require_aware(timestamp: datetime, *, field: str) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _require_finite_positive(value: Decimal, *, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive Decimal")


def _validate_pair(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> None:
    if current.timestamp <= previous.timestamp:
        raise ValueError("current timestamp must be strictly newer than previous timestamp")
    if current.source_id != previous.source_id:
        raise ValueError("source_id must remain constant across order-flow observations")
    if current.instrument_id != previous.instrument_id:
        raise ValueError("instrument_id must remain constant across order-flow observations")


def compute_ofi(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> Decimal:
    _validate_pair(previous, current)

    with deterministic_decimal_context():
        bid_term = _ZERO
        if current.best_bid >= previous.best_bid:
            bid_term += current.bid_size
        if current.best_bid <= previous.best_bid:
            bid_term -= previous.bid_size

        ask_term = _ZERO
        if current.best_ask <= previous.best_ask:
            ask_term -= current.ask_size
        if current.best_ask >= previous.best_ask:
            ask_term += previous.ask_size

        return bid_term + ask_term


def normalized_ofi(
    previous: TopOfBookObservation,
    current: TopOfBookObservation,
) -> Decimal:
    _validate_pair(previous, current)

    with deterministic_decimal_context():
        depth_scale = (
            previous.bid_size + previous.ask_size + current.bid_size + current.ask_size
        ) / Decimal(4)
        if depth_scale <= 0:
            raise ValueError("average top-of-book depth must be positive")
        return compute_ofi(previous, current) / depth_scale


def microprice(book: TopOfBookObservation) -> Decimal:
    with deterministic_decimal_context():
        total_depth = book.bid_size + book.ask_size
        if total_depth <= 0:
            raise ValueError("top-of-book depth must be positive")
        return (book.best_ask * book.bid_size + book.best_bid * book.ask_size) / total_depth


def microprice_displacement(book: TopOfBookObservation) -> Decimal:
    with deterministic_decimal_context():
        return (microprice(book) - book.mid) / book.mid


@dataclass(frozen=True, slots=True)
class OfiImpactConfig:
    window: int
    min_samples: int
    min_abs_feature_energy: Decimal
    max_abs_beta: Decimal
    score_scale_vol_units: Decimal

    def __post_init__(self) -> None:
        if self.window <= 0:
            raise ValueError("window must be positive")
        if self.min_samples <= 0 or self.min_samples > self.window:
            raise ValueError("min_samples must be within [1, window]")
        _require_finite_positive(
            self.min_abs_feature_energy,
            field="min_abs_feature_energy",
        )
        _require_finite_positive(self.max_abs_beta, field="max_abs_beta")
        _require_finite_positive(self.score_scale_vol_units, field="score_scale_vol_units")


@dataclass(frozen=True, slots=True)
class OfiImpactState:
    samples: tuple[OfiImpactSample, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(sample, OfiImpactSample) for sample in self.samples):
            raise ValueError("samples must contain OfiImpactSample values")


@dataclass(frozen=True, slots=True)
class OfiImpactEstimate:
    beta: Decimal | None
    fit_r2: Decimal | None
    sample_count: int
    ready: bool

    def __post_init__(self) -> None:
        if self.sample_count < 0:
            raise ValueError("sample_count must be non-negative")
        for field_name in ("beta", "fit_r2"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError(f"{field_name} must be a finite Decimal when available")
        if self.ready and (self.beta is None or self.fit_r2 is None):
            raise ValueError("ready OFI impact estimate requires beta and fit_r2")

    @classmethod
    def not_ready(
        cls,
        *,
        sample_count: int,
        beta: Decimal | None = None,
        fit_r2: Decimal | None = None,
    ) -> "OfiImpactEstimate":
        return cls(beta=beta, fit_r2=fit_r2, sample_count=sample_count, ready=False)


def _sample_sort_key(sample: OfiImpactSample) -> tuple[datetime, datetime, Decimal, Decimal]:
    return (
        sample.matured_at,
        sample.feature_timestamp,
        sample.normalized_ofi,
        sample.relative_price_change,
    )


def _eligible_samples(
    state: OfiImpactState,
    *,
    decision_time: datetime,
    window: int,
) -> tuple[OfiImpactSample, ...]:
    _require_aware(decision_time, field="decision_time")
    matured = sorted(
        (sample for sample in state.samples if sample.matured_at <= decision_time),
        key=_sample_sort_key,
    )
    return tuple(matured[-window:])


def estimate_ofi_impact(
    state: OfiImpactState,
    *,
    decision_time: datetime,
    config: OfiImpactConfig,
) -> OfiImpactEstimate:
    samples = _eligible_samples(state, decision_time=decision_time, window=config.window)
    sample_count = len(samples)
    if sample_count < config.min_samples:
        return OfiImpactEstimate.not_ready(sample_count=sample_count)

    with deterministic_decimal_context():
        feature_energy = sum(
            (sample.normalized_ofi * sample.normalized_ofi for sample in samples),
            start=_ZERO,
        )
        if feature_energy < config.min_abs_feature_energy:
            return OfiImpactEstimate.not_ready(sample_count=sample_count)

        cross = sum(
            (sample.normalized_ofi * sample.relative_price_change for sample in samples),
            start=_ZERO,
        )
        raw_beta = cross / feature_energy
        beta = min(config.max_abs_beta, max(-config.max_abs_beta, raw_beta))
        squared_error = sum(
            (
                (
                    sample.relative_price_change
                    - beta * sample.normalized_ofi
                )
                ** 2
                for sample in samples
            ),
            start=_ZERO,
        )
        target_energy = sum(
            (sample.relative_price_change * sample.relative_price_change for sample in samples),
            start=_ZERO,
        )
        fit_r2 = _ONE if target_energy == 0 else _ONE - squared_error / target_energy

    return OfiImpactEstimate(
        beta=beta,
        fit_r2=fit_r2,
        sample_count=sample_count,
        ready=True,
    )


def update_ofi_impact(
    state: OfiImpactState,
    sample: OfiImpactSample,
    *,
    decision_time: datetime,
    config: OfiImpactConfig,
) -> tuple[OfiImpactState, OfiImpactEstimate]:
    _require_aware(decision_time, field="decision_time")
    all_samples = (*state.samples, sample)
    matured = sorted(
        (item for item in all_samples if item.matured_at <= decision_time),
        key=_sample_sort_key,
    )
    pending = sorted(
        (item for item in all_samples if item.matured_at > decision_time),
        key=_sample_sort_key,
    )
    next_state = OfiImpactState(samples=(*matured[-config.window :], *pending))
    estimate = estimate_ofi_impact(next_state, decision_time=decision_time, config=config)
    return next_state, estimate


def predict_ofi_displacement(
    normalized_ofi_value: Decimal,
    estimate: OfiImpactEstimate,
) -> Decimal | None:
    if (
        not isinstance(normalized_ofi_value, Decimal)
        or not normalized_ofi_value.is_finite()
    ):
        raise ValueError("normalized_ofi_value must be a finite Decimal")
    if not estimate.ready or estimate.beta is None:
        return None
    with deterministic_decimal_context():
        return estimate.beta * normalized_ofi_value


__all__ = [
    "OfiImpactConfig",
    "OfiImpactEstimate",
    "OfiImpactState",
    "compute_ofi",
    "estimate_ofi_impact",
    "microprice",
    "microprice_displacement",
    "normalized_ofi",
    "predict_ofi_displacement",
    "update_ofi_impact",
]

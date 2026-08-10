from grid_trade.domain.instrument import require_instruments_compatible
from grid_trade.domain.market import MarketSnapshot
from grid_trade.domain.orders import PassiveOrderIntent
from grid_trade.strategy.grid_geometry import FixedLongGridConfig, build_long_grid_at_center


def build_fixed_long_grid(
    snapshot: MarketSnapshot,
    config: FixedLongGridConfig,
    generation: int,
) -> tuple[PassiveOrderIntent, ...]:
    require_instruments_compatible(
        snapshot.instrument_id,
        config.instrument_id,
        context="fixed-grid snapshot/config",
    )
    return build_long_grid_at_center(
        snapshot.mid,
        config,
        generation=generation,
        stage="s0",
    )


__all__ = ["FixedLongGridConfig", "build_fixed_long_grid"]

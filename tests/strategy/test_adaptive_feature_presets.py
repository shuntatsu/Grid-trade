from grid_trade.strategy.adaptive_grid import AdaptiveStage
from grid_trade.strategy.features import AdaptiveFeatures


def test_stage_presets_preserve_historical_activation() -> None:
    assert AdaptiveFeatures.from_stage(AdaptiveStage.S3_INVENTORY) == AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=False,
        conditional_reversal=False,
        funding_bias=False,
        order_book_reference=False,
    )
    assert AdaptiveFeatures.from_stage(AdaptiveStage.S7_ORDER_BOOK) == AdaptiveFeatures(
        inventory_control=True,
        partial_derisk=True,
        conditional_reversal=True,
        funding_bias=True,
        order_book_reference=True,
    )

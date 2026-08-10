from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AdaptiveFeatures:
    inventory_control: bool
    partial_derisk: bool
    conditional_reversal: bool
    funding_bias: bool
    order_book_reference: bool

    def __post_init__(self) -> None:
        for field_name in (
            "inventory_control",
            "partial_derisk",
            "conditional_reversal",
            "funding_bias",
            "order_book_reference",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a bool")

    @classmethod
    def from_stage(cls, stage: int) -> "AdaptiveFeatures":
        stage_value = int(stage)
        if not 3 <= stage_value <= 7:
            raise ValueError("adaptive stage must be within [3, 7]")
        return cls(
            inventory_control=True,
            partial_derisk=stage_value >= 4,
            conditional_reversal=stage_value >= 5,
            funding_bias=stage_value >= 6,
            order_book_reference=stage_value >= 7,
        )


__all__ = ["AdaptiveFeatures"]

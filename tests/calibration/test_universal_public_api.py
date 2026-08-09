from grid_trade.calibration import (
    UniversalCalibrationConfig,
    UniversalCalibrationState,
    UniversalCalibrationUpdate,
    update_universal_calibration,
)


def test_universal_calibration_is_exported_from_package() -> None:
    assert UniversalCalibrationConfig is not None
    assert UniversalCalibrationState is not None
    assert UniversalCalibrationUpdate is not None
    assert callable(update_universal_calibration)

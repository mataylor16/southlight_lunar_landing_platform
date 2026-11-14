from app import landing_status


def test_nominal_landing_status():
    assert landing_status(fuel=72, altitude=1800, velocity=12) == "NOMINAL"


def test_critical_landing_status_for_low_fuel():
    assert landing_status(fuel=10, altitude=1800, velocity=12) == "CRITICAL"


def test_caution_landing_status_for_low_altitude():
    assert landing_status(fuel=72, altitude=500, velocity=12) == "CAUTION"
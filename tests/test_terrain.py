import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from terrain import read_terrain


def test_preview_preserves_pixel_centers_and_masks_nodata(tmp_path):
    path = tmp_path / "terrain.tif"
    values = np.array([[10, -9999], [30, 40]], dtype="float32")
    with rasterio.open(path, "w", driver="GTiff", width=2, height=2,
                       count=1, dtype="float32", nodata=-9999,
                       transform=from_origin(1000, 2000, 5, 5)) as output:
        output.write(values, 1)
    result = read_terrain(path)
    np.testing.assert_allclose(result["x"], [1.0025, 1.0075])
    np.testing.assert_allclose(result["y"], [1.9975, 1.9925])
    assert np.isnan(result["z"][0, 1])
    preview = read_terrain(path, max_side=1)
    assert preview["z"].shape == (1, 1)
    assert preview["z"][0, 0] == pytest.approx((10 + 30 + 40) / 3)
    assert preview["preview_resolution"] == 10

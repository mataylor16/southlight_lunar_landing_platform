"""NASA LOLA south-pole elevation downloads and interactive terrain views."""

import base64
import os
from pathlib import Path
import tempfile

import numpy as np
import plotly.graph_objects as go
import rasterio
from rasterio.enums import Resampling
import requests
import streamlit as st


SOURCE = "https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/"
GOKUTO_AVATAR = Path(__file__).parent / "assets" / "gokuto-avatar.png"
SITES = {
    "Shackleton rim": "Site04",
    "Connecting ridge": "Site01",
    "Peak near Shackleton": "Site07",
    "de Gerlache rim": "Site11",
    "Leibnitz beta plateau": "Site20",
    "Malapert massif": "Site23",
}


@st.cache_data(max_entries=1, show_spinner=False)
def gokuto_avatar_html():
    """Return Gokuto's transparent cutout inside the terrain explainer avatar frame."""
    image_data = base64.b64encode(GOKUTO_AVATAR.read_bytes()).decode("ascii")
    return f'''<div style="width: 8rem; height: 8rem; overflow: hidden; border: 2px solid #f4bfff; border-radius: 50%; position: relative; box-sizing: border-box;">
      <img src="data:image/png;base64,{image_data}" alt="Gokuto" style="width: 8rem; height: 8rem; object-fit: contain;" />
    </div>'''


def source_url(site: str) -> str:
    if site not in SITES.values():
        raise ValueError("Unknown LOLA site")
    return f"{SOURCE}{site}/{site}_final_adj_5mpp_surf.tif"


def read_terrain(path, max_side=500):
    """Read a bounded, nodata-aware preview retaining projected pixel centers."""
    with rasterio.open(path) as dataset:
        scale = min(1.0, max_side / max(dataset.width, dataset.height))
        width = max(1, round(dataset.width * scale))
        height = max(1, round(dataset.height * scale))
        elevation = dataset.read(
            1, out_shape=(height, width), masked=True,
            resampling=Resampling.average,
        ).astype("float64").filled(np.nan)
        elevation[~np.isfinite(elevation)] = np.nan
        if not np.isfinite(elevation).any():
            raise ValueError("This elevation file contains no valid terrain pixels.")
        transform = dataset.transform * dataset.transform.scale(
            dataset.width / width, dataset.height / height,
        )
        if transform.b != 0 or transform.d != 0:
            raise ValueError("Rotated rasters are not supported.")
        x = (transform.c + (np.arange(width) + 0.5) * transform.a) / 1000
        y = (transform.f + (np.arange(height) + 0.5) * transform.e) / 1000
        return {
            "z": elevation, "x": x, "y": y,
            "native_resolution": abs(dataset.transform.a),
            "preview_resolution": abs(transform.a),
        }


@st.cache_data(ttl=86400, max_entries=6, show_spinner=False)
def load_terrain(site):
    """Keep successful downloads on disk; never cache partial transfers."""
    url = source_url(site)
    cache = Path(tempfile.gettempdir()) / "southlight-lola"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{site}.tif"
    if not target.exists():
        temporary = None
        try:
            with requests.get(url, stream=True, timeout=(15, 120)) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(dir=cache, suffix=".part", delete=False) as output:
                    temporary = Path(output.name)
                    size = 0
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > 512 * 1024 * 1024:
                            raise ValueError("NASA terrain file exceeds the 512 MB download limit.")
                        output.write(chunk)
            data = read_terrain(temporary)
            os.replace(temporary, target)
            return data
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    try:
        return read_terrain(target)
    except (rasterio.errors.RasterioError, ValueError):
        target.unlink(missing_ok=True)
        raise


def render_terrain():
    st.subheader("Moon surface atlas")
    st.write("Browse the south-pole ridgelines and crater rims captured by NASA's Lunar Orbiter Laser Altimeter.")
    with st.container(key="gokuto_terrain_explainer"):
        portrait, explanation = st.columns([2, 9], vertical_alignment="top", wrap=False)
        with portrait:
            st.html(gokuto_avatar_html(), width="content")
        with explanation:
            with st.container(border=True):
                st.markdown(
                    "**Gokuto says:** You’re looking at a height map of the Moon’s south pole. "
                    "Purple areas are lower and yellow areas are higher. Switch to 3D to explore "
                    "the hills and crater rims.\n\n"
                    "How do we know the heights? NASA’s Lunar Reconnaissance Orbiter carried "
                    "LOLA, a laser altimeter. It sent tiny laser pulses down to the Moon and "
                    "timed how long the light took to bounce back. That tells scientists how "
                    "far the surface is, and many measurements together make this map.\n\n"
                    "The heights are measured from a smooth, imaginary Moon called a "
                    "**reference sphere**. Think of it as the zero line: hills can be above it "
                    "and dips below it. It isn’t the real bumpy ground, so these numbers don’t "
                    "tell how high a lander is above the nearby surface."
                )
    controls, info = st.columns([1, 2])
    with controls:
        label = st.selectbox("Landing region", list(SITES))
        view = st.segmented_control("Terrain view", ["Elevation map", "3D surface"], default="Elevation map")
        exaggeration = st.slider("Vertical exaggeration (3D)", 1, 10, 1)
    site = SITES[label]
    with info:
        st.info("Elevation is surface height relative to the lunar reference sphere, not lander altitude. "
                "Map axes use south-polar stereographic coordinates in the MOON_ME / DE421 frame.")
        st.markdown(f"[NASA dataset and documentation]({SOURCE}) · [Download {site} GeoTIFF]({source_url(site)})")
    try:
        with st.spinner(f"Loading NASA LOLA terrain for {label}. First download may take a minute…"):
            data = load_terrain(site)
    except (requests.RequestException, rasterio.errors.RasterioError, OSError, ValueError) as exc:
        st.error(f"Could not load NASA terrain: {exc}")
        st.write("Check your internet connection and NASA data availability, then retry.")
        if st.button("Retry terrain download"):
            load_terrain.clear()
            st.rerun()
        return
    z, x, y = data["z"], data["x"], data["y"]
    low, high, resolution = st.columns(3)
    low.metric("Lowest elevation", f"{np.nanmin(z):,.0f} m")
    high.metric("Highest elevation", f"{np.nanmax(z):,.0f} m")
    resolution.metric("Source pixel size", f"{data['native_resolution']:g} m")
    hover = "X: %{x:.2f} km<br>Y: %{y:.2f} km<br>Elevation: %{z:,.0f} m<extra></extra>"
    if view == "Elevation map":
        figure = go.Figure(go.Heatmap(
            x=x, y=y, z=z, colorscale=[[0, "#24132f"], [0.25, "#542674"], [0.55, "#9746bd"], [0.8, "#e39ee9"], [1, "#ffd447"]], colorbar={"title": {"text": "Elevation (m)", "font": {"color": "#f7eaff"}}, "tickfont": {"color": "#f7eaff"}},
            hovertemplate=hover, hoverongaps=False,
        ))
        figure.update_layout(xaxis_title="Polar X (km)", yaxis_title="Polar Y (km)")
        figure.update_yaxes(scaleanchor="x", scaleratio=1)
    else:
        figure = go.Figure(go.Surface(
            x=x, y=y, z=z / 1000, surfacecolor=z, colorscale=[[0, "#24132f"], [0.25, "#542674"], [0.55, "#9746bd"], [0.8, "#e39ee9"], [1, "#ffd447"]],
            colorbar={"title": {"text": "Elevation (m)", "font": {"color": "#f7eaff"}}, "tickfont": {"color": "#f7eaff"}}, customdata=z,
            hovertemplate="X: %{x:.2f} km<br>Y: %{y:.2f} km<br>Elevation: %{customdata:,.0f} m<extra></extra>",
            connectgaps=False,
        ))
        spans = [max(float(np.ptp(x)), 0.001), max(float(np.ptp(y)), 0.001),
                 max(float(np.nanmax(z) - np.nanmin(z)) / 1000, 0.001) * exaggeration]
        spans = np.array(spans) / max(spans)
        figure.update_layout(scene={
            "xaxis_title": "Polar X (km)", "yaxis_title": "Polar Y (km)",
            "zaxis_title": "Elevation (km)", "aspectmode": "manual",
            "aspectratio": dict(zip(["x", "y", "z"], spans)),
        })
    figure.update_layout(height=620, margin={"l": 0, "r": 0, "t": 25, "b": 0},
                         paper_bgcolor="#1c102b", plot_bgcolor="#1c102b",
                         font={"color": "#f7eaff"})
    st.plotly_chart(figure, width="stretch")
    st.caption(
        f"Preview averages the native raster to approximately {data['preview_resolution']:.1f} m/pixel "
        "(maximum 500 pixels per side); extrema describe this preview. Missing terrain stays blank. "
        f"3D vertical exaggeration: {exaggeration}×."
    )
    st.caption("Terrain inspection only. Sun/Earth positions, terrain shadows, and communication windows "
               "have not yet been calculated. Source: Barker et al. (2021), NASA GSFC PGDA / LRO LOLA.")

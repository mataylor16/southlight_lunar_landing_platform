from pathlib import Path
from datetime import date, datetime, timedelta
import csv
from io import BytesIO
import re

import numpy as np
import plotly.graph_objects as go
import requests
import rasterio
import streamlit as st
from rasterio.enums import Resampling
from rasterio.io import MemoryFile
from terrain import render_terrain

def landing_status(fuel: int, altitude: int, velocity: int) -> str:
    """Return a simple flight status for the landing dashboard."""
    if fuel <= 10 or altitude <= 100 and velocity >= 40:
        return "CRITICAL"
    if altitude <= 500 or velocity >= 25:
        return "CAUTION"
    return "NOMINAL"


# NASA Lunar Surface Data Book center coordinates for PGDA LOLA candidate regions.
# Format: east longitude, latitude, degrees.
SITE_COORDINATES = {
    "Shackleton rim": (-171.87, -89.7668),
    "Connecting ridge": (-137.49, -89.4632),
    "Peak near Shackleton": (123.6901, -88.811),
    "de Gerlache rim": (-67.9321, -88.6834),
    "Leibnitz beta plateau": (31.74276, -85.4266),
    "Malapert massif": (-0.23578, -85.9948),
}
SITE_COLORS = {
    "Shackleton rim": "#ff3333",
    "Connecting ridge": "#33cc33",
    "Peak near Shackleton": "#3388ff",
    "de Gerlache rim": "#ffcc00",
    "Leibnitz beta plateau": "#ff33cc",
    "Malapert massif": "#00d5ff",
}
SITE_COLOR_MARKERS = {
    "Shackleton rim": "\U0001F534",
    "Connecting ridge": "\U0001F7E2",
    "Peak near Shackleton": "\U0001F535",
    "de Gerlache rim": "\U0001F7E1",
    "Leibnitz beta plateau": "\U0001F7E3",
    "Malapert massif": "\U0001F535",
}
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
GLOBAL_LOLA_URL = "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_4.tif"


@st.cache_data(ttl=21600, max_entries=24, show_spinner=False)
def fetch_horizons_track(target, longitude, latitude, day):
    """Fetch one UTC day of lunar-surface azimuth/elevation from JPL Horizons."""
    next_day = day + timedelta(days=1)
    params = {
        "format": "json",
        "COMMAND": f"'{target}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'OBSERVER'",
        "CENTER": "'coord@301'",
        "COORD_TYPE": "'GEODETIC'",
        "SITE_COORD": f"'{longitude},{latitude},0'",
        "START_TIME": f"'{day:%Y-%b-%d} 00:00'",
        "STOP_TIME": f"'{next_day:%Y-%b-%d} 00:00'",
        "STEP_SIZE": "'2 h'",
        "TIME_TYPE": "'UT'",
        "TIME_DIGITS": "'MINUTES'",
        "QUANTITIES": "'4'",
        "CSV_FORMAT": "'YES'",
    }
    response = requests.get(HORIZONS_URL, params=params, timeout=(10, 45))
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise ValueError(payload["error"])
    result = payload.get("result", "")
    match = re.search(r"\$\$SOE(.*?)\$\$EOE", result, flags=re.DOTALL)
    if not match:
        raise ValueError("Horizons returned no ephemeris rows for this site and date.")
    rows = list(csv.reader(match.group(1).strip().splitlines(), skipinitialspace=True))
    header_text = result.split("$$SOE", 1)[0]
    header = next((row for row in csv.reader(header_text.splitlines(), skipinitialspace=True)
                   if any("Azi_" in cell for cell in row)), None)
    if header is None:
        raise ValueError("Horizons CSV output is missing azimuth/elevation columns.")
    azimuth_index = next(i for i, cell in enumerate(header) if "Azi_" in cell)
    elevation_index = next(i for i, cell in enumerate(header) if "Elev_" in cell)
    points = []
    for row in rows:
        if len(row) <= max(azimuth_index, elevation_index) or not re.match(r"^\s*\d{4}-[A-Za-z]{3}-\d{2}", row[0]):
            continue
        try:
            timestamp_text = row[0].strip().strip('"')
            timestamp = datetime.strptime(timestamp_text, "%Y-%b-%d %H:%M")
            azimuth = float(row[azimuth_index].strip())
            elevation = float(row[elevation_index].strip())
        except (ValueError, IndexError):
            continue
        points.append({"time": timestamp, "azimuth": azimuth, "elevation": elevation})
    if not points:
        raise ValueError("Horizons returned data in an unrecognized format.")
    return points


def horizon_window(sun_track, earth_track, selected_hour, site_color):
    """Plot daily JPL Horizons tracks and mark the selected UTC hour."""
    fig = go.Figure()
    for track, name, color, symbol in [
        (sun_track, "Sun", site_color, "star"),
        (earth_track, "Earth", site_color, "circle"),
    ]:
        fig.add_trace(go.Scatterpolar(
            r=[90 - point["elevation"] for point in track],
            theta=[point["azimuth"] for point in track],
            mode="lines", name=f"{name} path",
            line={"color": color, "width": 2, "dash": "dot"},
            customdata=[[point["time"].strftime("%H:%M UTC"), point["elevation"]] for point in track],
            hovertemplate=f"{name}<br>%{{customdata[0]}}<br>Azimuth: %{{theta:.1f}} deg<br>Elevation: %{{customdata[1]:.1f}} deg<extra></extra>",
        ))
        current = min(track, key=lambda point: abs(point["time"].hour + point["time"].minute / 60 - selected_hour))
        fig.add_trace(go.Scatterpolar(
            r=[90 - current["elevation"]], theta=[current["azimuth"]],
            mode="markers+text", text=[name], textposition="top center",
            name=name, marker={"size": 15, "color": color, "symbol": symbol,
                "line": {"color": "#fff8ff", "width": 1}},
            customdata=[[current["time"].strftime("%H:%M UTC"), current["elevation"]]],
            hovertemplate=f"{name} at %{{customdata[0]}}<br>Azimuth: %{{theta:.1f}} deg<br>Elevation: %{{customdata[1]:.1f}} deg<extra></extra>",
        ))
    fig.update_layout(
        height=320, margin={"l": 35, "r": 35, "t": 28, "b": 25},
        paper_bgcolor="#251533", font={"color": "#fff4ff", "size": 11},
        showlegend=False,
        polar={"bgcolor": "#251533",
            "angularaxis": {"direction": "clockwise", "rotation": 90,
                "gridcolor": "#604273", "linecolor": "#76538a", "tickfont": {"color": "#e8d8f3"}},
            "radialaxis": {"range": [0, 90], "tickvals": [0, 30, 60, 90],
                "ticktext": ["90 deg", "60 deg", "30 deg", "0 deg"], "gridcolor": "#604273", "linecolor": "#76538a",
                "tickfont": {"color": "#d9c8e5"}}},
    )
    return fig


def lunar_site_locator(selected_sites, zoom=1):
    """Show selected sites on a schematic south-polar map of the Moon."""
    radial_limit = 5.5 / zoom
    fig = go.Figure()
    for name in selected_sites:
        longitude, latitude = SITE_COORDINATES[name]
        fig.add_trace(go.Scatterpolar(
            r=[90 + latitude], theta=[longitude], mode="markers", name=name,
            marker={
                "size": 13,
                "color": SITE_COLORS[name],
                "line": {"color": "#fff8ff", "width": 1},
            },
            hovertemplate=f"{name}<br>{latitude:.4f} deg latitude<br>{longitude:.4f} deg east longitude<extra></extra>",
        ))
    fig.update_layout(
        height=300, margin={"l": 35, "r": 35, "t": 24, "b": 20},
        paper_bgcolor="#251533", plot_bgcolor="#251533",
        font={"color": "#fff4ff", "size": 10}, showlegend=True,
        dragmode="zoom", uirevision="lunar-site-locator",
        legend={"orientation": "h", "y": -0.12, "x": 0.5, "xanchor": "center",
                "font": {"color": "#fff4ff", "size": 10}},
        polar={
            "bgcolor": "#251533",
            "angularaxis": {"direction": "clockwise", "rotation": 90,
                "gridcolor": "#604273", "linecolor": "#76538a",
                "tickfont": {"color": "#d9c8e5"}},
            "radialaxis": {"range": [0, radial_limit],
                "tickvals": [radius for radius in (1, 3, 5) if radius <= radial_limit],
                "ticktext": [f"{90 - radius:g} S" for radius in (1, 3, 5) if radius <= radial_limit],
                "gridcolor": "#604273",
                "linecolor": "#76538a", "tickfont": {"color": "#d9c8e5"}},
        },
        annotations=[{"text": "90 S<br>pole", "x": 0.5, "y": 0.5,
                      "xref": "paper", "yref": "paper", "showarrow": False,
                      "font": {"color": "#d9c8e5", "size": 9}}],
    )
    return fig


@st.cache_data(ttl=86400, max_entries=1, show_spinner=False)
def load_global_lola_preview():
    """Load NASA's small global LOLA DEM and reduce it for interactive 3D display."""
    response = requests.get(GLOBAL_LOLA_URL, timeout=(10, 60))
    response.raise_for_status()
    with MemoryFile(BytesIO(response.content)) as memory_file:
        with memory_file.open() as dataset:
            elevation_km = dataset.read(
                1, out_shape=(180, 360), masked=True,
                resampling=Resampling.average,
            ).astype("float32").filled(np.nan)
    return elevation_km


def lunar_globe(selected_sites):
    """Create a rotatable global lunar relief model with selected site markers."""
    elevation = load_global_lola_preview()
    height, width = elevation.shape
    latitude = 90 - (np.arange(height) + 0.5) * 180 / height
    longitude = -180 + (np.arange(width) + 0.5) * 360 / width
    lat_grid, lon_grid = np.meshgrid(np.deg2rad(latitude), np.deg2rad(longitude), indexing="ij")

    # The 20x relief makes the global LOLA elevation visible at this overview scale.
    radius = (1737.4 + elevation * 20) / 1737.4
    x = radius * np.cos(lat_grid) * np.cos(lon_grid)
    y = radius * np.cos(lat_grid) * np.sin(lon_grid)
    z = radius * np.sin(lat_grid)
    figure = go.Figure(go.Surface(
        x=x, y=y, z=z, surfacecolor=elevation,
        colorscale=[[0, "#37343a"], [0.35, "#77737a"], [0.7, "#bcb8b0"], [1, "#f0ece2"]],
        cmin=float(np.nanpercentile(elevation, 2)),
        cmax=float(np.nanpercentile(elevation, 98)),
        showscale=False, connectgaps=False,
        lighting={"ambient": 0.48, "diffuse": 0.72, "specular": 0.12, "roughness": 0.9},
        hoverinfo="skip",
        name="LOLA global elevation",
    ))
    for name in selected_sites:
        longitude_deg, latitude_deg = SITE_COORDINATES[name]
        lat_rad, lon_rad = np.deg2rad(latitude_deg), np.deg2rad(longitude_deg)
        marker_radius = 1.003
        figure.add_trace(go.Scatter3d(
            x=[marker_radius * np.cos(lat_rad) * np.cos(lon_rad)],
            y=[marker_radius * np.cos(lat_rad) * np.sin(lon_rad)],
            z=[marker_radius * np.sin(lat_rad)],
            mode="markers", name=name,
            marker={"size": 7, "color": SITE_COLORS[name], "line": {"color": "#ffffff", "width": 1}},
            customdata=[[latitude_deg, longitude_deg]],
            hovertemplate=f"{name}<br>%{{customdata[0]:.4f}} deg latitude<br>%{{customdata[1]:.4f}} deg east longitude<extra></extra>",
        ))
    figure.update_layout(
        height=390, margin={"l": 0, "r": 0, "t": 8, "b": 0},
        paper_bgcolor="#251533", font={"color": "#fff4ff", "size": 10},
        scene={"bgcolor": "#251533", "aspectmode": "data",
            "xaxis": {"visible": False}, "yaxis": {"visible": False}, "zaxis": {"visible": False},
            "camera": {"eye": {"x": 1.65, "y": -2.3, "z": -1.25}}},
        showlegend=False,
    )
    return figure


def render_planner():
    st.markdown("## Site and sky comparison")
    st.write("Compare JPL Horizons Sun and Earth positions above candidate lunar landing sites.")
    filters = st.columns([1, 2, 1], vertical_alignment="bottom")
    with filters[0]:
        selected_date = st.date_input("Planning date (UTC)", value=date.today(), key="planner_date")
    with filters[1]:
        selected_sites = st.multiselect(
            "Landing sites", list(SITE_COORDINATES),
            default=["Shackleton rim", "Connecting ridge", "Malapert massif"],
            format_func=lambda name: f"{SITE_COLOR_MARKERS[name]}  {name}",
            key="planner_sites",
        )
    with filters[2]:
        selected_hour = st.slider("UTC hour", 0, 24, 12, key="planner_hour")

    st.caption("Each dotted path samples the selected UTC day every 2 hours. The highlighted markers show the nearest sample to the selected hour.")
    st.markdown("### Lunar context")
    st.caption("Selected landing sites are marked in their assigned colors. The 3D globe uses NASA LOLA global elevation; relief is exaggerated 20x for visibility.")
    locator_chart, globe_chart = st.columns(2, vertical_alignment="center")
    with locator_chart:
        st.markdown("#### South-pole locator")
        locator_zoom = st.slider(
            "Locator zoom", min_value=1, max_value=10, value=1, step=1,
            format="%dx", key="lunar_locator_zoom",
            help="Zoom toward the south pole. Outer landing sites may move outside the view at higher zoom levels.",
        )
        st.plotly_chart(
            lunar_site_locator(selected_sites, locator_zoom), width="stretch", key="lunar_site_locator",
            config={"displaylogo": False},
        )
    with globe_chart:
        st.markdown("#### 3D Moon")
        try:
            with st.spinner("Loading NASA global lunar elevation model..."):
                st.plotly_chart(lunar_globe(selected_sites), width="stretch", key="lunar_globe")
        except (requests.RequestException, rasterio.errors.RasterioError, ValueError) as exc:
            st.warning(f"Could not load the NASA 3D Moon model: {exc}")
    st.caption("The Sun and Earth are shown relative to a flat horizon; local terrain blocking is not calculated.")
    st.caption("Ephemerides: [JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) · Site coordinates: [NASA Lunar Surface Data Book](https://ntrs.nasa.gov/citations/20250002845)")
    if not selected_sites:
        st.warning("Select one or more sites to compare their sky windows.")
        return

    for start in range(0, len(selected_sites), 3):
        row_sites = selected_sites[start:start + 3]
        for column, name in zip(st.columns(len(row_sites)), row_sites):
            with column:
                with st.container(border=True):
                    longitude, latitude = SITE_COORDINATES[name]
                    st.markdown(f"#### {name}")
                    st.caption(f"{latitude:.4f} deg, {longitude:.4f} deg E / {selected_date:%d %b %Y}")
                    try:
                        with st.spinner(f"Loading Horizons ephemeris for {name}..."):
                            sun = fetch_horizons_track("10", longitude, latitude, selected_date)
                            earth = fetch_horizons_track("399", longitude, latitude, selected_date)
                        st.plotly_chart(horizon_window(sun, earth, selected_hour, SITE_COLORS[name]),
                                        width="stretch", key=f"window_{name}")
                        st.caption("Horizon: 0 deg elevation / overhead: 90 deg")
                    except (requests.RequestException, ValueError, KeyError) as exc:
                        st.error(f"Could not load JPL Horizons data: {exc}")


def main():
    st.set_page_config(
        page_title="Southlight Lunar Landing",
        page_icon="moon",
        layout="wide",
    )

    st.image(Path(__file__).parent / "assets" / "moon_penguin.jpeg", alt="Penguin sitting on a glowing crescent moon beneath three stars", width=170)
    st.title("Southlight lunar site planner")
    st.caption("Compare candidate landing sites, lunar terrain, and sky positions.")

    planner_tab, terrain_tab, flight_tab = st.tabs(["Site comparison", "Terrain explorer", "Flight status"])
    with planner_tab:
        render_planner()
    with terrain_tab:
        render_terrain()

    with st.sidebar:
        st.markdown("### â˜¾  Southlight")
        st.caption("LUNAR FIELD DESK Â· MISSION 01")
        st.markdown("---")
        st.header("Flight controls")
        fuel = st.slider("Fuel remaining (%)", 0, 100, 72)
        altitude = st.slider("Altitude (m)", 0, 5000, 1800, step=50)
        velocity = st.slider("Vertical velocity (m/s)", 0, 100, 12)

    status = landing_status(fuel, altitude, velocity)
    status_message = {
        "NOMINAL": st.success,
        "CAUTION": st.warning,
        "CRITICAL": st.error,
    }[status]

    with flight_tab:
        left, middle, right = st.columns(3)
        left.metric("Fuel", f"{fuel}%")
        middle.metric("Altitude", f"{altitude:,} m")
        right.metric("Vertical velocity", f"{velocity} m/s")
        st.divider()
        st.subheader("Landing status")
        status_message(f"Flight status: {status}")
        st.markdown("#### Mission readout")
        st.progress(fuel / 100, text="Propellant reserve")
        st.progress(min(altitude / 5000, 1), text="Descent altitude")
        st.progress(min(velocity / 100, 1), text="Vertical velocity")
        st.caption("Adjust the flight controls in the sidebar to update this readout.")


if __name__ == "__main__":
    main()



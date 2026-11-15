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
SOLAR_IRRADIANCE_W_M2 = 1361
CLASSROOM_PANEL_EFFICIENCY = 0.20


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


def visible_hours(track):
    """Estimate time above a flat horizon from Horizons' two-hour samples."""
    return sum(
        (later["time"] - earlier["time"]).total_seconds() / 3600
        * (int(earlier["elevation"] > 0) + int(later["elevation"] > 0)) / 2
        for earlier, later in zip(track, track[1:])
    )


def simultaneous_hours(sun_track, earth_track):
    """Estimate time when the Sun and Earth are both above a flat horizon."""
    return sum(
        (later_sun["time"] - earlier_sun["time"]).total_seconds() / 3600
        * (
            int(earlier_sun["elevation"] > 0 and earlier_earth["elevation"] > 0)
            + int(later_sun["elevation"] > 0 and later_earth["elevation"] > 0)
        ) / 2
        for (earlier_sun, later_sun), (earlier_earth, later_earth) in zip(
            zip(sun_track, sun_track[1:]), zip(earth_track, earth_track[1:])
        )
    )


def solar_panel_illustration(panel_area):
    """Draw a simple lunar rover with one square-meter tile per panel area unit."""
    columns = min(panel_area, 5)
    rows = (panel_area + columns - 1) // columns
    panel_left = 2.85
    panel_bottom = 1.65 - rows / 2
    panel_right = panel_left + columns
    scene_right = panel_right + 0.55

    figure = go.Figure()
    figure.add_shape(type="line", x0=-0.45, y0=0.5, x1=scene_right, y1=0.5,
                     line={"color": "#765c87", "width": 2})
    # Cartoon rover wheels and chassis.
    for wheel_x in (0.75, 2.15):
        figure.add_shape(type="circle", x0=wheel_x - 0.43, x1=wheel_x + 0.43,
                         y0=0.48, y1=1.34, fillcolor="#34283e",
                         line={"color": "#c9b9d4", "width": 2})
        figure.add_shape(type="circle", x0=wheel_x - 0.16, x1=wheel_x + 0.16,
                         y0=0.75, y1=1.07, fillcolor="#ffd447",
                         line={"color": "#fff0a8", "width": 1})
    figure.add_shape(type="rect", x0=0.38, x1=2.52, y0=1.12, y1=2.12,
                     fillcolor="#b8a8c4", line={"color": "#f7eaff", "width": 2})
    figure.add_shape(type="rect", x0=0.63, x1=1.28, y0=1.5, y1=1.91,
                     fillcolor="#251533", line={"color": "#7edcff", "width": 2})
    figure.add_shape(type="rect", x0=1.55, x1=2.27, y0=1.42, y1=1.82,
                     fillcolor="#755d87", line={"color": "#ddd0e7", "width": 1})
    # High-gain antenna and the arm that carries the deployable panel wing.
    figure.add_shape(type="line", x0=1.8, y0=2.12, x1=1.8, y1=2.87,
                     line={"color": "#d8c8e4", "width": 3})
    figure.add_shape(type="circle", x0=1.57, x1=2.03, y0=2.74, y1=3.2,
                     fillcolor="#8ce4ff", line={"color": "#e7faff", "width": 2})
    figure.add_shape(type="line", x0=2.52, y0=1.67, x1=panel_left, y1=1.67,
                     line={"color": "#d8c8e4", "width": 5})

    # Gold squares are literal 1 m x 1 m area tiles in this simplified view.
    for index in range(panel_area):
        row, column = divmod(index, columns)
        x0 = panel_left + column
        y0 = panel_bottom + (rows - row - 1)
        figure.add_shape(type="rect", x0=x0, x1=x0 + 1, y0=y0, y1=y0 + 1,
                         fillcolor="#ffd447", line={"color": "#fff0a8", "width": 2})
        figure.add_shape(type="line", x0=x0 + 0.48, x1=x0 + 0.48,
                         y0=y0 + 0.05, y1=y0 + 0.91,
                         line={"color": "#bd8d19", "width": 1})
    figure.add_annotation(x=(panel_left + panel_right) / 2, y=panel_bottom + rows + 0.18,
                          text=f"{panel_area} m² solar array", showarrow=False,
                          font={"color": "#ffe58a", "size": 12})
    figure.update_layout(
        height=225, margin={"l": 4, "r": 4, "t": 4, "b": 4},
        paper_bgcolor="#251533", plot_bgcolor="#251533",
        showlegend=False, hovermode=False,
        xaxis={"range": [-0.5, scene_right], "visible": False, "fixedrange": True},
        yaxis={"range": [0.3, 3.45], "visible": False, "fixedrange": True,
               "scaleanchor": "x", "scaleratio": 1},
    )
    return figure


def horizon_infographic():
    """Explain flat-horizon visibility and how a ridge can block a low target."""
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=[0, 5.2, 5.2, 0], y=[0.75, 0.75, 0.2, 0.2],
        fill="toself", fillcolor="#382846", line={"width": 0},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[5.6, 7.15, 8.15, 9.15, 11.2, 11.2, 5.6],
        y=[0.75, 0.75, 3.15, 0.75, 0.75, 0.2, 0.2],
        fill="toself", fillcolor="#382846", line={"color": "#806890", "width": 2},
        hoverinfo="skip", showlegend=False,
    ))
    # The dotted zero-degree line gives each observer a level, local reference.
    for x0, x1 in ((0.35, 5.0), (5.75, 11.0)):
        figure.add_shape(type="line", x0=x0, y0=1.35, x1=x1, y1=1.35,
                         line={"color": "#c5a9dc", "width": 1, "dash": "dot"})
    # Clear case: a low Sun is above the local level line and has an open view.
    figure.add_trace(go.Scatter(
        x=[1.25, 4.45], y=[1.35, 2.55], mode="lines",
        line={"color": "#ffd447", "width": 2, "dash": "dash"},
        hoverinfo="skip", showlegend=False,
    ))
    # Ridge case: show a broken sightline to the same low target and the block point.
    figure.add_trace(go.Scatter(
        x=[6.35, 7.62], y=[1.35, 1.82], mode="lines",
        line={"color": "#ffd447", "width": 2, "dash": "dash"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[1.25, 6.35], y=[1.0, 1.0], mode="markers",
        marker={"size": 11, "color": "#8ce4ff", "symbol": "circle", "line": {"color": "#ffffff", "width": 1}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[4.45, 10.45], y=[2.55, 2.9], mode="markers",
        marker={"size": 18, "color": "#ffd447", "symbol": "star", "line": {"color": "#fff0a8", "width": 1}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[7.62], y=[1.82], mode="markers",
        marker={"size": 13, "color": "#ff6b7a", "symbol": "x", "line": {"width": 3}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.update_layout(
        height=205, margin={"l": 4, "r": 4, "t": 4, "b": 4},
        paper_bgcolor="#251533", plot_bgcolor="#251533",
        showlegend=False, hovermode=False,
        xaxis={"range": [0, 11.3], "visible": False, "fixedrange": True},
        yaxis={"range": [0.2, 3.6], "visible": False, "fixedrange": True},
        annotations=[
            {"x": 2.6, "y": 3.35, "text": "Above 0°: visible in the flat-horizon model", "showarrow": False,
             "font": {"color": "#fff4ff", "size": 11}},
            {"x": 8.4, "y": 3.45, "text": "A ridge can block a low Sun or Earth", "showarrow": False,
             "font": {"color": "#fff4ff", "size": 11}},
            {"x": 2.0, "y": 1.07, "text": "observer", "showarrow": False, "yshift": -17,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 6.75, "y": 1.07, "text": "observer", "showarrow": False, "yshift": -17,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 2.45, "y": 1.42, "text": "0° flat horizon", "showarrow": False,
             "font": {"color": "#d9c8e5", "size": 9}},
            {"x": 9.65, "y": 1.42, "text": "0° flat horizon", "showarrow": False,
             "font": {"color": "#d9c8e5", "size": 9}},
            {"x": 7.62, "y": 1.82, "text": "blocked", "showarrow": True, "ax": 35, "ay": -21,
             "font": {"color": "#ff9aa3", "size": 9}, "arrowcolor": "#ff6b7a"},
        ],
    )
    return figure


def communication_infographic():
    """Show how solar power and a clear Earth link work together for a rover."""
    figure = go.Figure()
    figure.add_trace(go.Scatter(
        x=[0, 5.3, 5.3, 0], y=[0.68, 0.68, 0.2, 0.2],
        fill="toself", fillcolor="#382846", line={"width": 0},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[5.65, 8.25, 9.05, 9.9, 11.4, 11.4, 5.65],
        y=[0.68, 0.68, 3.05, 0.68, 0.68, 0.2, 0.2],
        fill="toself", fillcolor="#382846", line={"color": "#806890", "width": 2},
        hoverinfo="skip", showlegend=False,
    ))

    def draw_rover(offset):
        for wheel_x in (offset + 1.72, offset + 2.48):
            figure.add_shape(type="circle", x0=wheel_x - 0.22, x1=wheel_x + 0.22,
                             y0=0.58, y1=1.02, fillcolor="#34283e",
                             line={"color": "#c9b9d4", "width": 1})
        figure.add_shape(type="rect", x0=offset + 1.5, x1=offset + 2.7, y0=0.85, y1=1.65,
                         fillcolor="#b8a8c4", line={"color": "#f7eaff", "width": 1})
        figure.add_shape(type="rect", x0=offset + 1.68, x1=offset + 2.04, y0=1.18, y1=1.5,
                         fillcolor="#251533", line={"color": "#8ce4ff", "width": 1})
        figure.add_shape(type="line", x0=offset + 2.38, y0=1.65, x1=offset + 2.38, y1=2.12,
                         line={"color": "#e5d8ef", "width": 2})
        figure.add_shape(type="circle", x0=offset + 2.23, x1=offset + 2.53,
                         y0=2.02, y1=2.32, fillcolor="#8ce4ff",
                         line={"color": "#e7faff", "width": 1})
        # A small gold two-by-two array represents the rover's solar panels.
        for row in range(2):
            for col in range(2):
                x0 = offset + 0.48 + col * 0.38
                y0 = 0.95 + row * 0.38
                figure.add_shape(type="rect", x0=x0, x1=x0 + 0.34, y0=y0, y1=y0 + 0.34,
                                 fillcolor="#ffd447", line={"color": "#fff0a8", "width": 1})
        figure.add_shape(type="line", x0=offset + 1.24, y0=1.34,
                         x1=offset + 1.5, y1=1.34,
                         line={"color": "#d8c8e4", "width": 2})

    draw_rover(0)
    draw_rover(5.65)

    # Sunlight feeds the rover's panels; Earth and radio links use blue.
    figure.add_trace(go.Scatter(
        x=[0.52, 1.05], y=[3.1, 1.58], mode="lines",
        line={"color": "#ffd447", "width": 2, "dash": "dot"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[6.17, 6.7], y=[3.1, 1.58], mode="lines",
        line={"color": "#ffd447", "width": 2, "dash": "dot"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[0.52, 6.17], y=[3.1, 3.1], mode="markers",
        marker={"size": 15, "color": "#ffd447", "symbol": "star"},
        hoverinfo="skip", showlegend=False,
    ))
    # Open direct-to-Earth link on the left.
    figure.add_trace(go.Scatter(
        x=[2.38, 4.45], y=[2.15, 2.65], mode="lines",
        line={"color": "#65d9ff", "width": 3, "dash": "dot"},
        hoverinfo="skip", showlegend=False,
    ))
    # A ridge interrupts the direct signal on the right; an orbital relay provides another path.
    figure.add_trace(go.Scatter(
        x=[8.03, 8.37], y=[2.15, 2.26], mode="lines",
        line={"color": "#65d9ff", "width": 3, "dash": "dot"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[8.37], y=[2.26], mode="markers",
        marker={"size": 13, "color": "#ff6b7a", "symbol": "x", "line": {"width": 3}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[8.03, 9.18], y=[2.15, 4.08], mode="lines",
        line={"color": "#8ce4ff", "width": 2, "dash": "dot"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[9.18, 11.03], y=[4.08, 2.65], mode="lines",
        line={"color": "#65d9ff", "width": 2, "dash": "dash"},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[4.45, 11.03], y=[2.65, 2.65], mode="markers",
        marker={"size": 20, "color": "#3388ff", "symbol": "circle", "line": {"color": "#e7faff", "width": 1}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.add_trace(go.Scatter(
        x=[9.18], y=[4.08], mode="markers",
        marker={"size": 15, "color": "#8ce4ff", "symbol": "diamond", "line": {"color": "#ffffff", "width": 1}},
        hoverinfo="skip", showlegend=False,
    ))
    figure.update_layout(
        height=225, margin={"l": 4, "r": 4, "t": 4, "b": 4},
        paper_bgcolor="#251533", plot_bgcolor="#251533",
        showlegend=False, hovermode=False,
        xaxis={"range": [0, 11.5], "visible": False, "fixedrange": True},
        yaxis={"range": [0.2, 4.6], "visible": False, "fixedrange": True},
        annotations=[
            {"x": 2.6, "y": 4.08, "text": "Earth in view: direct radio link", "showarrow": False,
             "font": {"color": "#fff4ff", "size": 11}},
            {"x": 9.0, "y": 4.58, "text": "Ridge blocks direct link; relay can help", "showarrow": False,
             "font": {"color": "#fff4ff", "size": 11}},
            {"x": 4.48, "y": 2.96, "text": "Earth", "showarrow": False,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 11.02, "y": 2.96, "text": "Earth", "showarrow": False,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 9.18, "y": 4.08, "text": "lunar relay", "showarrow": False, "yshift": 16,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 8.45, "y": 3.15, "text": "Rover to relay", "showarrow": False,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 10.15, "y": 3.65, "text": "Relay to Earth", "showarrow": False,
             "font": {"color": "#bdefff", "size": 9}},
            {"x": 6.17, "y": 3.1, "text": "Sun", "showarrow": False, "xshift": -19,
             "font": {"color": "#ffe58a", "size": 9}},
            {"x": 0.52, "y": 3.1, "text": "Sun", "showarrow": False, "xshift": -19,
             "font": {"color": "#ffe58a", "size": 9}},
        ],
    )
    return figure


def render_mission_challenge(site_tracks, selected_sites):
    """Turn ephemeris tracks into a simple, explicitly approximate site choice."""
    st.markdown("### Your Moon mission")
    st.write("Your crew needs sunlight to charge its batteries and a view of Earth to call home. Which site would you choose for this UTC day?")
    with st.container(border=True):
        st.markdown("#### What does ‘above a flat horizon’ mean?")
        st.caption("The flat horizon is an imaginary level line at 0°. Above it means the Sun or Earth could be in view if the ground were perfectly level. A nearby ridge or crater rim can raise the real skyline and block it.")
        st.plotly_chart(
            horizon_infographic(), width="stretch", key="horizon_explainer",
            config={"displayModeBar": False},
        )
    illustration, = st.columns(1)
    panel_area = st.session_state.get("classroom_panel_area", 2)
    with illustration:
        st.markdown("#### How can we use the Sun for power?")
        st.caption("Change the slider to see how a larger solar array could collect more sunlight.")
        st.plotly_chart(
            solar_panel_illustration(panel_area), width="stretch", key="solar_panel_preview",
            config={"displayModeBar": False},
        )
        st.caption(
            f"Each gold tile represents 1 m². Cartoon rover inspired by NASA's "
            f"[VIPER design](https://science.nasa.gov/mission/viper/lunar-operations/); the vehicle and array are simplified, not to scale."
        )
        panel_area = st.slider(
            "Solar panel area (square meters)", 1, 10, 2, key="classroom_panel_area",
            help="Imagine a panel that turns to face the Sun. We assume 20% of the incoming sunlight becomes electricity.",
        )
        st.caption("A kilowatt-hour (kWh) is an amount of energy. A 100 W device uses 1 kWh in 10 hours.")
    with st.container(border=True):
        st.markdown("#### How do we call Earth?")
        st.caption("Sunlight charges the batteries that power the radio. A direct message needs Earth in view, but a ridge can block that path. Think of an orbiting relay as a space mailbox: it receives the rover's message, then sends it onward to Earth. The relay must be able to see the rover and have a path to Earth or another relay. As satellites move, coverage changes; several relays can fill more gaps. See [NASA's lunar relay plans](https://www.nasa.gov/communicating-with-missions/lunanet/).")
        st.plotly_chart(
            communication_infographic(), width="stretch", key="communication_explainer",
            config={"displayModeBar": False},
        )
    st.markdown("### Site information")
    outlooks = {}
    for name, tracks in site_tracks.items():
        sun, earth = tracks
        sun_hours = visible_hours(sun)
        earth_hours = visible_hours(earth)
        outlooks[name] = {
            "sun_hours": sun_hours,
            "earth_hours": earth_hours,
            "both_hours": simultaneous_hours(sun, earth),
            "energy_kwh": sun_hours * SOLAR_IRRADIANCE_W_M2 * CLASSROOM_PANEL_EFFICIENCY * panel_area / 1000,
        }

    for start in range(0, len(selected_sites), 3):
        row = [name for name in selected_sites[start:start + 3] if name in outlooks]
        if not row:
            continue
        for column, name in zip(st.columns(len(row)), row):
            result = outlooks[name]
            with column, st.container(border=True):
                st.markdown(f"#### {SITE_COLOR_MARKERS[name]} {name}")
                st.metric("Sun above horizon", f"{result['sun_hours']:.0f} h")
                st.metric("Possible solar energy", f"{result['energy_kwh']:.1f} kWh")
                st.metric("Earth above horizon", f"{result['earth_hours']:.0f} h")
                st.caption(f"Sun and Earth together: about {result['both_hours']:.0f} h")

    if not outlooks:
        st.warning("Mission estimates are unavailable until the Horizons data loads.")
        return

    choice = st.selectbox(
        "Choose your landing site", list(outlooks),
        format_func=lambda name: f"{SITE_COLOR_MARKERS[name]}  {name}",
        key="classroom_site_choice",
    )
    chosen = outlooks[choice]
    most_sun = max(outlooks, key=lambda name: outlooks[name]["sun_hours"])
    most_earth = max(outlooks, key=lambda name: outlooks[name]["earth_hours"])
    st.info(
        f"At **{choice}**, the Sun is above a flat horizon for about **{chosen['sun_hours']:.0f} hours**, "
        f"and Earth is above it for about **{chosen['earth_hours']:.0f} hours**. "
        f"For this date, **{most_sun}** has the most modeled sunlight and **{most_earth}** has the longest possible direct Earth view "
        "among your selected sites. A longer Earth view gives more chances to call home. "
        f"With a perfect battery, this site's estimated solar energy could run an example 100 W radio "
        f"for about **{chosen['energy_kwh'] * 10:.0f} hours**; the radio could call Earth only while Earth is visible "
        "or through a relay."
    )
    st.caption(
        "Classroom estimate: NASA Horizons samples every 2 hours; a Sun-facing panel converts 20% of about 1,361 W/m² "
        "for the time the Sun is above a flat horizon. Energy = sunlight hours × panel area × 1,361 × 0.20 ÷ 1,000. "
        "Hills can block the low Sun or Earth, so these numbers are possibilities, not actual power or guaranteed radio contact. "
        "The 100 W radio and perfect battery are teaching examples, not spacecraft specifications. "
        "Panel losses, batteries, radio design, and relay satellites are not modeled. This does not rate landing safety."
    )
    with st.expander("Where do these ideas come from?"):
        st.markdown(
            "- [NASA Space Place: Sun](https://spaceplace.nasa.gov/menu/sun/) explains the Sun for younger explorers.\n"
            "- [NASA: The lunar south pole region](https://www.nasa.gov/reference/moonbase-environment/) explains why sunlight and shadow matter for power.\n"
            "- [NASA: Lunar surface propagation](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/scan/lunar-surface-propagation/) explains why seeing Earth matters for radio contact.\n"
            "- [NASA: Solar irradiance](https://sunclimate.gsfc.nasa.gov/article/solar-irradiance) gives about 1,361 W/m² in space.\n"
            "- [JPL Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html) supplies the Sun and Earth positions used here."
        )


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
    st.markdown("## Choose a Moon landing site")
    st.write("Compare real Sun and Earth positions above candidate lunar landing sites, then decide where your crew should go.")
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

    site_tracks = {}
    for name in selected_sites:
        longitude, latitude = SITE_COORDINATES[name]
        try:
            with st.spinner(f"Loading Sun and Earth positions for {name}..."):
                sun = fetch_horizons_track("10", longitude, latitude, selected_date)
                earth = fetch_horizons_track("399", longitude, latitude, selected_date)
            site_tracks[name] = (sun, earth)
        except (requests.RequestException, ValueError, KeyError) as exc:
            st.warning(f"Could not load Horizons data for {name}: {exc}")

    render_mission_challenge(site_tracks, selected_sites)
    st.markdown("### Look at the sky")
    st.caption("These charts show where the Sun and Earth appear from each landing site.")

    for start in range(0, len(selected_sites), 3):
        row_sites = selected_sites[start:start + 3]
        for column, name in zip(st.columns(len(row_sites)), row_sites):
            with column:
                with st.container(border=True):
                    longitude, latitude = SITE_COORDINATES[name]
                    st.markdown(f"#### {name}")
                    st.caption(f"{latitude:.4f} deg, {longitude:.4f} deg E / {selected_date:%d %b %Y}")
                    if name in site_tracks:
                        sun, earth = site_tracks[name]
                        st.plotly_chart(horizon_window(sun, earth, selected_hour, SITE_COLORS[name]),
                                        width="stretch", key=f"window_{name}")
                        st.caption("Horizon: 0 deg elevation / overhead: 90 deg")
                    else:
                        st.caption("Sky positions are unavailable for this site and date.")


def main():
    st.set_page_config(
        page_title="Southlight Lunar Landing",
        page_icon="moon",
        layout="wide",
    )

    st.image(Path(__file__).parent / "assets" / "moon_penguin.jpeg", alt="Penguin sitting on a glowing crescent moon beneath three stars", width=170)
    st.title("Southlight lunar site planner")
    st.caption("Explore landing sites, lunar terrain, sunlight, and ways to call Earth.")

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



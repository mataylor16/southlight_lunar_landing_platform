from pathlib import Path
from datetime import date

import plotly.graph_objects as go
import streamlit as st
from terrain import render_terrain

def landing_status(fuel: int, altitude: int, velocity: int) -> str:
    """Return a simple flight status for the landing dashboard."""
    if fuel <= 10 or altitude <= 100 and velocity >= 40:
        return "CRITICAL"
    if altitude <= 500 or velocity >= 25:
        return "CAUTION"
    return "NOMINAL"


SITE_WINDOWS = {
    "Shackleton rim": {"sun": (42, 12), "earth": (118, 26)},
    "Connecting ridge": {"sun": (76, 8), "earth": (205, 31)},
    "Peak near Shackleton": {"sun": (132, 18), "earth": (38, 22)},
    "de Gerlache rim": {"sun": (205, 9), "earth": (316, 18)},
    "Leibnitz beta plateau": {"sun": (285, 16), "earth": (102, 34)},
    "Malapert massif": {"sun": (334, 22), "earth": (248, 15)},
}


def horizon_window(site):
    """Draw an illustrative sky window pending Horizons ephemeris integration."""
    fig = go.Figure()
    for azimuth, elevation, name, color, symbol in [
        (*site["sun"], "Sun", "#ffd447", "star"),
        (*site["earth"], "Earth", "#72cfff", "circle"),
    ]:
        fig.add_trace(go.Scatterpolar(
            r=[90 - elevation], theta=[azimuth], customdata=[elevation], mode="markers+text", text=[name],
            textposition="top center", name=name,
            marker={"size": 17, "color": color, "symbol": symbol,
                "line": {"color": "#fff8ff", "width": 1}},
            hovertemplate=f"{name}<br>Azimuth: %{{theta:.0f}} deg<br>Elevation: %{{customdata:.0f}} deg<extra></extra>",
        ))
    fig.update_layout(
        height=310, margin={"l": 35, "r": 35, "t": 28, "b": 25},
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


def render_planner():
    st.markdown("## Site and sky comparison")
    st.write("Compare where the Sun and Earth appear above candidate lunar landing sites.")
    filters = st.columns([1, 2, 1], vertical_alignment="bottom")
    with filters[0]:
        selected_date = st.date_input("Planning date", value=date.today(), key="planner_date")
    with filters[1]:
        selected_sites = st.multiselect(
            "Landing sites", list(SITE_WINDOWS),
            default=["Shackleton rim", "Connecting ridge", "Malapert massif"],
            key="planner_sites",
        )
    with filters[2]:
        st.metric("Sites selected", len(selected_sites))

    st.info(
        "Preview positions are illustrative placeholders. JPL Horizons is the intended source "
        "for date-specific Sun and Earth positions; they are not connected yet."
    )
    if not selected_sites:
        st.warning("Select one or more sites to compare their sky windows.")
        return

    for start in range(0, len(selected_sites), 3):
        row_sites = selected_sites[start:start + 3]
        for column, name in zip(st.columns(len(row_sites)), row_sites):
            with column:
                with st.container(border=True):
                    st.markdown(f"#### {name}")
                    st.caption(f"Lunar south pole / {selected_date:%d %b %Y}")
                    st.plotly_chart(horizon_window(SITE_WINDOWS[name]),
                                    width="stretch", key=f"window_{name}")
                    st.caption("Horizon: 0 deg elevation / overhead: 90 deg")


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



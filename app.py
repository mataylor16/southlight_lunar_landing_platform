import streamlit as st


def landing_status(fuel: int, altitude: int, velocity: int) -> str:
    """Return a simple flight status for the landing dashboard."""
    if fuel <= 10 or altitude <= 100 and velocity >= 40:
        return "CRITICAL"
    if altitude <= 500 or velocity >= 25:
        return "CAUTION"
    return "NOMINAL"


st.set_page_config(
    page_title="Southlight Lunar Landing",
    page_icon="moon",
    layout="wide",
)

st.title("Southlight Lunar Landing")
st.caption("A small Streamlit starter application running in Docker.")

with st.sidebar:
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

left, middle, right = st.columns(3)
left.metric("Fuel", f"{fuel}%")
middle.metric("Altitude", f"{altitude:,} m")
right.metric("Vertical velocity", f"{velocity} m/s")

st.divider()
st.subheader("Landing status")
status_message(f"Flight status: {status}")
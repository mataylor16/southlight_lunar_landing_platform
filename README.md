# Southlight Lunar Landing Platform

A minimal Docker-based Python Streamlit application managed with Poetry.

## LOLA terrain explorer

The default **LOLA terrain** tab loads NASA LRO/LOLA elevation models for six
lunar south-pole regions. Choose a region, inspect the elevation map, or switch
to the rotatable 3D surface. Vertical exaggeration is adjustable.

The first visit downloads the selected GeoTIFF from NASA GSFC (internet access
required). Successful downloads are cached in the system temporary directory
under `southlight-lola`. A new Docker container may need to download again.
Download failures show a retry control rather than synthetic terrain.

The source models have 5 m pixels; previews average to at most 500 pixels per
side. Heights are relative to the lunar reference sphere. Map axes are projected
polar coordinates, not longitude/latitude. Blank pixels indicate missing data.
Sun/Earth positions, terrain horizons, shadowing, power, and communication windows
are not calculated in this terrain inspection view.

Source: [NASA GSFC LOLA site products](https://pgda.gsfc.nasa.gov/data/LOLA_5mpp/),
Barker et al. (2021), *Improved LOLA Elevation Maps for South Pole Landing Sites*.

## JPL Horizons site comparison

The Site comparison page requests date-specific lunar-surface observer
ephemerides for the Sun and Earth from JPL Horizons. It shows a daily track at
two-hour intervals and highlights the sample nearest the selected UTC hour.
An accompanying south-pole locator places the candidate regions around the
lunar pole, alongside a rotatable 3D globe built from NASA's global LOLA
elevation model. The globe loads a 4 MB, 4-pixel-per-degree TIFF on first use
and exaggerates relief 20x for visibility. Candidate coordinates come from the NASA Lunar Surface Data Book.
Horizons positions do not account for local terrain blocking visibility.

## Run with Docker

Build and start the app:

```text
docker compose up --build
```

Open http://localhost:8501 in a browser. Stop the app with `Ctrl+C`, then remove
the container with:

```text
docker compose down
```

The repository is mounted into the container, so edits to `app.py` are picked up
by Streamlit's file watcher.

## Run tests

The development dependencies can be installed and tests run in a one-off
container:

```text
docker compose run --rm streamlit poetry install
docker compose run --rm streamlit pytest
```

## Local development

Python 3.12 and Poetry 2.x are the supported local tools. After installing them,
run `poetry install`, then `poetry run streamlit run app.py`.

The Docker setup handles Python dependencies and Streamlit execution. It does
not replace any platform-specific deployment or hosting configuration.

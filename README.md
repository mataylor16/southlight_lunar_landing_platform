# Southlight Lunar Landing Platform

A minimal Docker-based Python Streamlit application managed with Poetry.

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
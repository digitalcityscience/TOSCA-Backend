# docker/django/Dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_VENV_IN_PROJECT=1
ENV UV_VENV_CLEAR=1

# System dependencies for GeoDjango
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependency management

RUN pip install --upgrade pip \
    && pip install uv 

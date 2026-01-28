# docker/django/Dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_VENV_IN_PROJECT=1
ENV UV_VENV_CLEAR=1

# Sadece dependency tanımı

RUN pip install --upgrade pip \
 && pip install uv 

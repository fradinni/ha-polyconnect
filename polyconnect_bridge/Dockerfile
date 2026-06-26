FROM python:3.12-slim-bookworm

# Install system dependencies for Playwright/Chromium + mitmproxy
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Install Playwright + Chromium with all system deps
RUN playwright install chromium --with-deps

# Cache-bust: bump this value to force re-copy of source files
ARG CACHE_BUST=2.0.2

# Copy addon files
COPY run.sh /run.sh
COPY server.py /server.py
COPY capture_manager.py /capture_manager.py
COPY mitm_addon.py /mitm_addon.py
COPY setup_ui.py /setup_ui.py

RUN chmod +x /run.sh

# Ensure /data directory exists (Supervisor maps it, but ensure it exists for local dev)
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/run.sh"]

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

# Ensure /data directory exists (Supervisor maps it, but ensure it exists for local dev)
RUN mkdir -p /data

# Bust cache: changing this label forces Docker to rebuild subsequent layers
LABEL io.hass.version="2.0.3"

# Copy ALL addon source in a single layer (any file change invalidates this layer)
COPY . /app/
RUN cp /app/run.sh /run.sh && chmod +x /run.sh && \
    cp /app/server.py /server.py && \
    cp /app/capture_manager.py /capture_manager.py && \
    cp /app/mitm_addon.py /mitm_addon.py && \
    cp /app/setup_ui.py /setup_ui.py && \
    rm -rf /app/

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/run.sh"]

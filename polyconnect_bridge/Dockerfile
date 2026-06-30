FROM python:3.12-slim-bookworm

# Install system dependencies for Playwright/Chromium
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

# Copy ALL addon source in a single layer (any file change invalidates this layer)
COPY . /app/
RUN cp /app/run.sh /run.sh && chmod +x /run.sh && \
    cp /app/server.py /server.py && \
    cp /app/auth.py /auth.py && \
    cp /app/config.yaml /config.yaml && \
    rm -rf /app/

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/run.sh"]

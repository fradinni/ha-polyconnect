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

# Copy addon files
COPY run.sh /run.sh
COPY server.py /server.py

RUN chmod +x /run.sh

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/run.sh"]

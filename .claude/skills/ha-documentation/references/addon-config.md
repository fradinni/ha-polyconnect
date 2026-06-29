# Add-on Configuration (config.yaml)

## Required Fields

```yaml
name: "My Add-on"
version: "1.0.0"
slug: my_addon
description: "Description of the add-on"
arch:
  - amd64
  - aarch64
```

| Key | Type | Description |
|-----|------|-------------|
| name | string | Display name |
| version | string | Version string |
| slug | string | Unique within repository, URI-friendly |
| description | string | Short description |
| arch | list | Supported: aarch64, amd64 |

## Common Optional Fields

```yaml
url: "https://github.com/user/addon"
startup: application      # initialize, system, services, application, once
boot: auto               # auto, manual, manual_only
webui: "http://[HOST]:[PORT:8080]/"
ingress: true
ingress_port: 8099
panel_icon: mdi:puzzle
panel_title: "My Add-on"
homeassistant: "2022.10.5"   # Min HA version
hassio_api: true
hassio_role: default        # default, homeassistant, backup, manager, admin
homeassistant_api: true
host_network: false
host_dbus: false
privileged: []
full_access: false
```

## Network Ports

```yaml
ports:
  "8080/tcp": 8080
  "443/tcp": null          # Disabled by default
ports_description:
  "8080/tcp": "Web interface"
```

## Volume Mapping

```yaml
map:
  - type: share
    read_only: false
  - type: ssl
  - type: homeassistant_config
    read_only: false
    path: /config
  - type: media
  - type: backup
  - type: data             # Always mapped, always writable
```

Types: homeassistant_config, addon_config, ssl, addons, backup, share, media, all_addon_configs, data

## Options & Schema

```yaml
options:
  target: "beer"
  debug: false
schema:
  target: str
  debug: bool
```

Schema types: str, int, float, bool, email, url, port, match(REGEX), list(schema)

## Environment Variables

```yaml
environment:
  MY_VAR: "value"
```

## Dockerfile

```dockerfile
FROM ghcr.io/home-assistant/base:latest

RUN apk add --no-cache example_package

COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
```

## run.sh Script

```bash
#!/usr/bin/env bashio
CONFIG_PATH=/data/options.json

TARGET="$(bashio::config 'target')"
DEBUG="$(bashio::config 'debug')"

echo "Starting with target: $TARGET"
exec /app/server.py
```

## Discovery

```yaml
discovery:
  - my_service
```

## Services

```yaml
services:
  - mqtt:want
  - mysql:want
```

Functions: provide, want, need

## Stage & Backup

```yaml
stage: stable          # stable, experimental, deprecated
backup: hot            # hot, cold
backup_exclude:
  - "*.log"
  - cache/
```

Source: https://developers.home-assistant.io/docs/add-ons/configuration

# Add-on Repository

## Repository Configuration

Create `repository.yaml` at the root of the git repository:

```yaml
name: Name of repository
url: http://www.example/addons
maintainer: HomeAssistant Team <info@home-assistant.io>
```

| Key | Required | Description |
|-----|----------|-------------|
| name | yes | Repository name |
| url | no | Homepage URL |
| maintainer | no | Contact info |

## Installing a Repository

Users add repositories via: Supervisor panel → Store icon → paste URL → Save.

Generate a [my.home-assistant.io](https://my.home-assistant.io/create-link/) link for easy installation.

## File Structure

```
repository/
├── repository.yaml
├── addon_one/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── run.sh
│   ├── icon.png
│   ├── logo.png
│   ├── DOCS.md
│   ├── CHANGELOG.md
│   └── README.md
└── addon_two/
    └── ...
```

## Icons and Logos

- `icon.png`: 256x256 PNG
- `logo.png`: 256x256 PNG (or wider for branding)

Source: https://developers.home-assistant.io/docs/add-ons/repository

"""Config flow for Polyconnect — auto-discovers the bridge add-on if installed."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow, ConfigEntry
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.selector import (
    TextSelector, TextSelectorConfig, TextSelectorType,
    NumberSelector, NumberSelectorConfig,
)

from .const import DOMAIN, CONF_BRIDGE_URL, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, LOGGER

ADDON_SLUG_SUFFIX = "polyconnect_bridge"
ADDON_DEFAULT_URL = "http://homeassistant.local:8765"


async def _detect_addon_url(hass: HomeAssistant) -> str | None:
    """Return the bridge add-on URL if it is installed and running.

    Uses the Supervisor REST API (only available on HA OS / Supervised).
    Returns the direct container IP URL (more reliable than ingress, which
    requires additional auth headers and an open ingress session).

    The add-on slug has a repository-hash prefix (e.g. ``ecbbef75_polyconnect_bridge``)
    that varies per installation, so we list all add-ons first and find the one whose
    slug ends with ``_polyconnect_bridge`` rather than hard-coding the full slug.
    """
    try:
        import os
        import aiohttp
        # SUPERVISOR_TOKEN is injected by the Supervisor into the HA Core container env
        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not supervisor_token:
            return None
        headers = {"Authorization": f"Bearer {supervisor_token}"}
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession() as s:
            # Step 1: list installed add-ons to resolve the hash-prefixed slug
            async with s.get(
                "http://supervisor/addons",
                headers=headers,
                timeout=timeout,
            ) as list_r:
                if list_r.status != 200:
                    return None
                list_data = await list_r.json()
                addons = list_data.get("data", {}).get("addons", [])
                slug = next(
                    (a["slug"] for a in addons if a.get("slug", "").endswith(ADDON_SLUG_SUFFIX)),
                    None,
                )
                if not slug:
                    LOGGER.debug("Polyconnect Bridge add-on not found in installed add-ons")
                    return None

            # Step 2: fetch the add-on details to get its container IP
            async with s.get(
                f"http://supervisor/addons/{slug}/info",
                headers=headers,
                timeout=timeout,
            ) as info_r:
                if info_r.status == 200:
                    info = await info_r.json()
                    data = info.get("data", {})
                    if data.get("state") == "started":
                        ip = data.get("ip_address", "")
                        if ip:
                            url = f"http://{ip}:8765"
                            LOGGER.debug("Auto-detected add-on URL: %s (slug=%s)", url, slug)
                            return url
    except Exception as e:
        LOGGER.debug("Could not detect add-on URL: %s", e)
    return None


class PolyconnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Setup wizard — auto-detects the bridge add-on, or accepts a manual URL."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        # Try to auto-detect the add-on URL for the default suggestion
        suggested_url = await _detect_addon_url(self.hass) or ADDON_DEFAULT_URL

        if user_input is not None:
            from .api import PolyconnectAPI, PolyconnectError

            url = user_input[CONF_BRIDGE_URL].rstrip("/")
            api = PolyconnectAPI(bridge_url=url)
            try:
                if not await api.health_check():
                    errors["base"] = "cannot_connect"
            except PolyconnectError:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error in config flow")
                errors["base"] = "unknown"
            finally:
                await api.close()

            if not errors:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Polyconnect Heat Pump",
                    data={CONF_BRIDGE_URL: url},
                    options={CONF_SCAN_INTERVAL: int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_BRIDGE_URL, default=suggested_url): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(min=10, max=3600, step=10, mode="box")
                ),
            }),
            errors=errors,
            description_placeholders={"addon_url": suggested_url},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return PolyconnectOptionsFlow()


class PolyconnectOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(min=10, max=3600, step=10, mode="box")
                ),
            }),
        )

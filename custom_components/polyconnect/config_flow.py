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

ADDON_SLUG = "polyconnect_bridge"
ADDON_DEFAULT_URL = "http://homeassistant.local:8765"


async def _detect_addon_url(hass: HomeAssistant) -> str | None:
    """Return the add-on ingress URL if the bridge add-on is installed and running."""
    try:
        # Ask the supervisor for the add-on info
        import aiohttp
        supervisor_token = hass.data.get("hassio", {}).get("config", {}).get("supervisor") or ""
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://supervisor/addons/{ADDON_SLUG}/info",
                headers={"Authorization": f"Bearer {supervisor_token}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    info = await r.json()
                    data = info.get("data", {})
                    if data.get("state") == "started":
                        # Use ingress URL if available, otherwise the exposed port
                        ingress = data.get("ingress_url", "")
                        if ingress:
                            LOGGER.debug("Auto-detected add-on ingress URL: %s", ingress)
                            return f"http://supervisor{ingress}"
    except Exception as e:
        LOGGER.debug("Could not detect add-on: %s", e)
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

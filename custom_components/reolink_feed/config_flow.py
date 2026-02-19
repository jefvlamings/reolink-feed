"""Config flow for Reolink feed."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv

from .const import CONF_ENABLED_LABELS, DEFAULT_ENABLED_DETECTION_LABELS, DOMAIN, SUPPORTED_DETECTION_LABELS


def _label_options() -> dict[str, str]:
    return {label: label.title() for label in SUPPORTED_DETECTION_LABELS}


class ReolinkFeedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Reolink feed."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create a single integration instance."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Reolink Feed", data={})

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> ReolinkFeedOptionsFlow:
        """Get options flow."""
        return ReolinkFeedOptionsFlow(config_entry)


class ReolinkFeedOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Reolink feed."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage options."""
        if user_input is not None:
            selected = [
                label
                for label in user_input.get(CONF_ENABLED_LABELS, [])
                if label in SUPPORTED_DETECTION_LABELS
            ]
            if not selected:
                selected = list(DEFAULT_ENABLED_DETECTION_LABELS)
            return self.async_create_entry(title="", data={CONF_ENABLED_LABELS: selected})

        existing = self._config_entry.options.get(CONF_ENABLED_LABELS)
        if isinstance(existing, list):
            default_labels = [label for label in existing if label in SUPPORTED_DETECTION_LABELS]
        else:
            default_labels = list(DEFAULT_ENABLED_DETECTION_LABELS)
        if not default_labels:
            default_labels = list(DEFAULT_ENABLED_DETECTION_LABELS)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENABLED_LABELS, default=default_labels): cv.multi_select(
                        _label_options()
                    ),
                }
            ),
        )

"""Config flow for Reolink feed."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class ReolinkFeedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Reolink feed."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Create a single integration instance."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title="Reolink Feed", data={})

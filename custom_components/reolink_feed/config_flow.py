"""Config flow for Reolink feed."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    CONF_CACHE_RECORDINGS,
    CONF_ENABLED_LABELS,
    CONF_MAX_DETECTIONS,
    CONF_REBUILD_NOW,
    CONF_RETENTION_HOURS,
    DEFAULT_CACHE_RECORDINGS,
    DEFAULT_ENABLED_DETECTION_LABELS,
    DEFAULT_MAX_DETECTIONS,
    DEFAULT_RETENTION_HOURS,
    DOMAIN,
    MAX_MAX_DETECTIONS,
    MAX_RETENTION_HOURS,
    MIN_MAX_DETECTIONS,
    MIN_RETENTION_HOURS,
    SUPPORTED_DETECTION_LABELS,
)


_LABEL_TITLES = {
    "en": {
        "person": "Person",
        "pet": "Pet",
        "vehicle": "Vehicle",
        "motion": "Motion",
        "visitor": "Visitor",
    },
    "nl": {
        "person": "Persoon",
        "pet": "Huisdier",
        "vehicle": "Voertuig",
        "motion": "Beweging",
        "visitor": "Bezoeker",
    },
}


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
            retention_hours = int(user_input.get(CONF_RETENTION_HOURS, DEFAULT_RETENTION_HOURS))
            retention_hours = max(MIN_RETENTION_HOURS, min(MAX_RETENTION_HOURS, retention_hours))
            max_detections = int(user_input.get(CONF_MAX_DETECTIONS, DEFAULT_MAX_DETECTIONS))
            max_detections = max(MIN_MAX_DETECTIONS, min(MAX_MAX_DETECTIONS, max_detections))
            rebuild_now = bool(user_input.get(CONF_REBUILD_NOW, False))
            cache_recordings = bool(user_input.get(CONF_CACHE_RECORDINGS, DEFAULT_CACHE_RECORDINGS))
            if rebuild_now:
                entry = next(
                    (
                        candidate
                        for candidate in self.hass.config_entries.async_entries(DOMAIN)
                        if candidate.entry_id == self._config_entry.entry_id
                    ),
                    None,
                )
                runtime_data = getattr(entry, "runtime_data", None) if entry else None
                manager = getattr(runtime_data, "manager", None)
                if manager is not None:
                    await manager.async_rebuild_from_history()
            return self.async_create_entry(
                title="",
                data={
                    CONF_ENABLED_LABELS: selected,
                    CONF_RETENTION_HOURS: retention_hours,
                    CONF_MAX_DETECTIONS: max_detections,
                    CONF_CACHE_RECORDINGS: cache_recordings,
                },
            )

        existing = self._config_entry.options.get(CONF_ENABLED_LABELS)
        if isinstance(existing, list):
            default_labels = [label for label in existing if label in SUPPORTED_DETECTION_LABELS]
        else:
            default_labels = list(DEFAULT_ENABLED_DETECTION_LABELS)
        if not default_labels:
            default_labels = list(DEFAULT_ENABLED_DETECTION_LABELS)
        retention_hours = int(
            self._config_entry.options.get(CONF_RETENTION_HOURS, DEFAULT_RETENTION_HOURS)
        )
        retention_hours = max(MIN_RETENTION_HOURS, min(MAX_RETENTION_HOURS, retention_hours))
        max_detections = int(
            self._config_entry.options.get(CONF_MAX_DETECTIONS, DEFAULT_MAX_DETECTIONS)
        )
        max_detections = max(MIN_MAX_DETECTIONS, min(MAX_MAX_DETECTIONS, max_detections))
        cache_recordings = bool(
            self._config_entry.options.get(CONF_CACHE_RECORDINGS, DEFAULT_CACHE_RECORDINGS)
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ENABLED_LABELS, default=default_labels): cv.multi_select(
                        self._label_options()
                    ),
                    vol.Required(CONF_RETENTION_HOURS, default=retention_hours): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_RETENTION_HOURS,
                            max=MAX_RETENTION_HOURS,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(CONF_MAX_DETECTIONS, default=max_detections): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_MAX_DETECTIONS,
                            max=MAX_MAX_DETECTIONS,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Optional(CONF_REBUILD_NOW, default=False): selector.BooleanSelector(),
                    vol.Required(
                        CONF_CACHE_RECORDINGS, default=cache_recordings
                    ): selector.BooleanSelector(),
                }
            ),
        )

    def _label_options(self) -> dict[str, str]:
        language = str(self.hass.config.language or "en").lower()
        base = language.split("-")[0]
        titles = _LABEL_TITLES.get(base, _LABEL_TITLES["en"])
        return {label: titles.get(label, label.title()) for label in SUPPORTED_DETECTION_LABELS}

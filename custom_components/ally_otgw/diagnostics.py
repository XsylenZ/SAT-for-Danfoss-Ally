"""Diagnostics: everything we are configured with and doing, in one download."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CLIMATE, COORDINATOR

# The attributes of the heating switch that explain what it is doing
HEATING_ATTRIBUTES = (
    "target_temperature", "outside_temperature", "heating_curve", "requested_setpoint", "setpoint", "minimum_setpoint",
    "error", "error_source", "heat_demand", "heat_allowed", "frost_guard", "failsafe",
    "pulse_width_modulation_active", "pulse_width_modulation_state", "pulse_width_modulation_duty_cycle",
)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return the settings of the entry, and the state of the boiler control and the TRVs."""
    diagnostics: dict[str, Any] = {
        "settings": dict(entry.data),
        "options": dict(entry.options),
    }

    if (data := hass.data.get(DOMAIN, {}).get(entry.entry_id)) is None:
        return diagnostics

    heating = data[CLIMATE]
    coordinator = data[COORDINATOR]
    attributes = heating.extra_state_attributes

    diagnostics["heating"] = {"on": heating.is_on, **{key: attributes.get(key) for key in HEATING_ATTRIBUTES}}
    diagnostics["zones"] = heating.areas.summary()
    diagnostics["trvs"] = heating.areas.overview()
    diagnostics["boiler"] = {
        "device_type": coordinator.device_type,
        "manufacturer": coordinator.manufacturer.friendly_name if coordinator.manufacturer is not None else None,
        "device_active": coordinator.device_active,
        "flame_active": coordinator.flame_active,
        "hot_water_active": coordinator.hot_water_active,
        "setpoint": coordinator.setpoint,
        "boiler_temperature": coordinator.boiler_temperature,
        "return_temperature": coordinator.return_temperature,
        "relative_modulation_value": coordinator.relative_modulation_value,
        "minimum_setpoint": coordinator.minimum_setpoint,
        "maximum_setpoint": coordinator.maximum_setpoint,
    }

    return diagnostics

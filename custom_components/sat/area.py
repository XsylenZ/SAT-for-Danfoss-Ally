import logging
from types import MappingProxyType
from typing import Any

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt

from .const import CONF_ROOMS, CONF_OVERSHOOT_HOLD, AREA_STATE_MAX_AGE
from .errors import Errors, Error
from .heating_curve import HeatingCurve
from .helpers import float_value
from .pid import PID
from .util import (
    create_pid_controller,
    create_heating_curve_controller,
)

_LOGGER = logging.getLogger(__name__)

SENSOR_TEMPERATURE_ID = "sensor_temperature_id"


class Area:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any], entity_id: str):
        self._entity_id: str = entity_id
        self._hass: HomeAssistant | None = None
        self._overshoot_hold: float = float(config_options.get(CONF_OVERSHOOT_HOLD))

        # Create controllers with the given configuration options
        self.pid: PID = create_pid_controller(config_options)
        self.heating_curve: HeatingCurve = create_heating_curve_controller(config_data, config_options)

    @property
    def id(self) -> str:
        return self._entity_id

    @property
    def state(self) -> State | None:
        """Retrieve the current state of the climate entity."""
        if (self._hass is None) or (state := self._hass.states.get(self._entity_id)) is None:
            return None

        return state if state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE] else None

    @property
    def target_temperature(self) -> float | None:
        """Retrieve the target temperature from the climate entity."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        return float_value(state.attributes.get("temperature"))

    @property
    def current_temperature(self) -> float | None:
        """Retrieve the current temperature, overridden by a sensor if set."""
        if (self._hass is None) or (state := self.state) is None:
            return None

        # Check if there is an overridden sensor temperature
        if sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID):
            sensor_state = self._hass.states.get(sensor_temperature_id)
            if sensor_state and sensor_state.state not in [STATE_UNKNOWN, STATE_UNAVAILABLE, HVACMode.OFF]:
                return float_value(sensor_state.state)

        return float_value(state.attributes.get("current_temperature") or self.target_temperature)

    @property
    def error(self) -> Error | None:
        """Calculate the temperature error, gated on whether the room is actually calling for heat."""
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        error = round(target_temperature - current_temperature, 2)
        hvac_action = self.state.attributes.get("hvac_action")

        # Thermostats that don't report an action, or that stopped reporting, behave like before
        if hvac_action is None or self.stale:
            return Error(self._entity_id, error)

        # The room is not calling for heat, so it should not drive the boiler
        if hvac_action != HVACAction.HEATING:
            return None

        # The room is calling for heat, keep requesting heat until its own thermostat is satisfied
        return Error(self._entity_id, max(error, self._overshoot_hold))

    @property
    def stale(self) -> bool:
        """Return True when the climate entity has not reported anything for too long."""
        if (state := self.state) is None:
            return False

        age = (dt.utcnow() - state.last_reported).total_seconds()
        if age <= AREA_STATE_MAX_AGE:
            return False

        _LOGGER.debug("Area %s stale (age=%.0fs > %ds), falling back to the plain error", self._entity_id, age, AREA_STATE_MAX_AGE)
        return True

    @property
    def weight(self) -> float | None:
        """
        Room heating demand weight (0-2 range).
        Based on the difference between target and current temperature.
        """
        target_temperature = self.target_temperature
        current_temperature = self.current_temperature

        if target_temperature is None or current_temperature is None:
            return None

        delta = target_temperature - current_temperature
        effective_delta = max(delta - 0.2, 0.0)
        raw_weight = effective_delta * 1.0

        return round(max(0.0, min(raw_weight, 2.0)), 3)

    async def async_added_to_hass(self, hass: HomeAssistant):
        self._hass = hass

    async def async_control_heating_loop(self, _time=None) -> None:
        """Asynchronously control the heating loop."""
        if self.error is None or self.heating_curve.value is None:
            return

        # Control the integral (if exceeded the time limit)
        self.pid.update_integral(self.error, self.heating_curve.value)


class Areas:
    def __init__(self, config_data: MappingProxyType[str, Any], config_options: MappingProxyType[str, Any]):
        """Initialize Areas with multiple Area instances using shared config data and options."""
        self._entity_ids: list[str] = config_data.get(CONF_ROOMS) or []
        self._areas: list[Area] = [Area(config_data, config_options, entity_id) for entity_id in self._entity_ids]

    @property
    def errors(self) -> Errors:
        """Return a list of all the error values for all areas."""
        return Errors([area.error for area in self._areas if area.error is not None])

    @property
    def demand(self) -> bool:
        """Return True when any area is calling for heat, or when no area can tell us."""
        # Without any available area we can't judge the demand, so behave like before
        if all(area.state is None for area in self._areas):
            return True

        return any(error.value > 0 for error in self.errors)

    @property
    def heating_curves(self):
        """Return an interface to update heating curves for all areas."""
        return Areas._HeatingCurves(self._areas)

    @property
    def pids(self):
        """Return an interface to reset PID controllers for all areas."""
        return Areas._PIDs(self._areas)

    def items(self) -> list[str]:
        return self._entity_ids

    def is_stale(self, entity_id: str) -> bool:
        """Return True when the area with the given entity id falls back to the plain error."""
        return any(area.id == entity_id and area.stale for area in self._areas)

    async def async_added_to_hass(self, hass: HomeAssistant):
        for area in self._areas:
            await area.async_added_to_hass(hass)

    async def async_control_heating_loops(self, _time=None) -> None:
        """Asynchronously control heating loop for all areas."""
        for area in self._areas:
            await area.async_control_heating_loop(_time)

    class _HeatingCurves:
        def __init__(self, areas: list[Area]):
            self.areas = areas

        def update(self, current_outside_temperature: float) -> None:
            """Update the heating curve for all areas based on the current outside temperature."""
            for area in self.areas:
                if area.target_temperature is None:
                    continue

                area.heating_curve.update(area.target_temperature, current_outside_temperature)

    class _PIDs:
        def __init__(self, areas: list[Area]):
            self.areas = areas

        def update(self, boiler_temperature: float) -> None:
            for area in self.areas:
                if area.error is not None:
                    area.pid.update(area.error, area.heating_curve.value, boiler_temperature)

        def reset(self) -> None:
            """Reset PID controllers for all areas."""
            for area in self.areas:
                area.pid.reset()

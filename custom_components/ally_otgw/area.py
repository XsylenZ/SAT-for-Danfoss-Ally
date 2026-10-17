import logging
from types import MappingProxyType
from typing import Any, Callable

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry
from homeassistant.util import dt

from .const import CONF_ZONES, CONF_OVERSHOOT_HOLD, AREA_STATE_MAX_AGE
from .errors import Errors, Error
from .heating_curve import HeatingCurve
from .helpers import float_value
from .pid import PID
from .util import (
    create_pid_controller,
    create_heating_curve_controller,
)
from .zones import Trv, Zone, discover_trvs, opening

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

        # Thermostats that stopped reporting can't be trusted, so they are left out just like unavailable ones
        if self.stale:
            return None

        error = round(target_temperature - current_temperature, 2)
        hvac_action = self.state.attributes.get("hvac_action")

        # Thermostats that don't report an action behave like before
        if hvac_action is None:
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

        _LOGGER.debug("Area %s stale (age=%.0fs > %ds), leaving it out", self._entity_id, age, AREA_STATE_MAX_AGE)
        return True

    @property
    def available(self) -> bool:
        """Return True when the climate entity is available and still reporting."""
        return self.state is not None and not self.stale

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
    def __init__(
            self,
            config_data: MappingProxyType[str, Any],
            config_options: MappingProxyType[str, Any],
            discover: Callable[[HomeAssistant, str], list[Trv]] = discover_trvs,
    ):
        """Initialize Areas from the configured zones, their TRVs are discovered once we know Home Assistant."""
        self._config_data = config_data
        self._config_options = config_options
        self._discover = discover
        self._hass: HomeAssistant | None = None

        self._areas: dict[str, Area] = {}
        self._zones: list[Zone] = [Zone(settings) for settings in config_options.get(CONF_ZONES) or []]
        self._unassigned: list[str] | None = None

    @property
    def configured(self) -> bool:
        """Return True when there are zones configured."""
        return len(self._zones) > 0

    @property
    def zones(self) -> list[Zone]:
        return self._zones

    @property
    def errors(self) -> Errors:
        """Return the error values of the TRVs in the zones that are calling for heat."""
        return Errors([
            error
            for zone in self._zones if zone.calling
            for trv in zone.trvs if (error := self._areas[trv.climate].error) is not None
        ])

    @property
    def demand(self) -> bool | None:
        """Return True when any zone is calling for heat, or None when no zone can tell us."""
        if not any(zone.available for zone in self._zones):
            return None

        return any(zone.calling for zone in self._zones)

    def refresh(self) -> bool:
        """Discover the TRVs of every zone again, returns True when anything changed."""
        if self._hass is None:
            return False

        changed = False
        areas: dict[str, Area] = {}

        for zone in self._zones:
            trvs = self._discover(self._hass, zone.area_id)
            if trvs != zone.trvs:
                _LOGGER.info("Zone %s now has the TRVs: %s", zone.area_id, ", ".join(trv.climate for trv in trvs) or "none")
                zone.trvs = trvs
                changed = True

            for trv in trvs:
                # Keep the controllers of the TRVs we already knew
                if (area := self._areas.get(trv.climate)) is None:
                    area = Area(self._config_data, self._config_options, trv.climate)
                    area._hass = self._hass

                areas[trv.climate] = area

        self._areas = areas
        self._log_unassigned()

        return changed

    def _log_unassigned(self) -> None:
        """Let the user know about TRVs that won't call for heat, and zones without any TRV."""
        if self._discover is not discover_trvs:
            return

        unassigned = [trv.climate for trv in discover_trvs(self._hass) if trv.climate not in self._areas]
        if unassigned == self._unassigned:
            return

        self._unassigned = unassigned

        if unassigned:
            _LOGGER.warning("These Danfoss Ally TRVs are not in any zone, so they won't call for heat: %s", ", ".join(unassigned))

        for zone in self._zones:
            if not zone.trvs:
                _LOGGER.warning("Zone %s doesn't contain any Danfoss Ally TRV", zone.area_id)

    def overview(self) -> list[dict[str, Any]]:
        """Return every Danfoss Ally TRV that Home Assistant knows about, and what we do with it."""
        if self._hass is None:
            return []

        registry = area_registry.async_get(self._hass)
        zones = {trv.climate: zone for zone in self._zones for trv in zone.trvs}

        def area_name(area_id: str | None) -> str | None:
            return (area.name if (area := registry.async_get_area(area_id)) is not None else area_id) if area_id else None

        result = []
        for trv in discover_trvs(self._hass):
            state = self._hass.states.get(trv.climate)
            area = self._areas.get(trv.climate)

            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                status = "unavailable"
            elif area is not None and area.stale:
                status = "stale"
            else:
                status = state.attributes.get("hvac_action") or state.state

            result.append({
                "entity_id": trv.climate,
                "name": state.name if state is not None else trv.climate,
                "area": area_name(trv.area_id),
                "zone": area_name(zones[trv.climate].area_id) if trv.climate in zones else None,
                "status": status,
                "current_temperature": state.attributes.get("current_temperature") if state is not None else None,
                "target_temperature": state.attributes.get("temperature") if state is not None else None,
                "opening": opening(self._hass, trv),
            })

        return result

    def update(self) -> None:
        """Determine which zones are calling for heat."""
        for zone in self._zones:
            zone.update(self._areas, self._hass)

    def target_temperature(self, entity_id: str) -> float | None:
        """Return the target temperature of the TRV with the given climate entity."""
        if (area := self._areas.get(entity_id)) is None:
            return None

        return area.target_temperature

    def summary(self) -> dict[str, Any]:
        """Return the state of every zone."""
        return {zone.area_id: zone.summary(self._hass) for zone in self._zones}

    @property
    def heating_curves(self):
        """Return an interface to update heating curves for all areas."""
        return Areas._HeatingCurves(list(self._areas.values()))

    @property
    def pids(self):
        """Return an interface to reset PID controllers for all areas."""
        return Areas._PIDs(list(self._areas.values()))

    def items(self) -> list[str]:
        """Return the climate entities of all TRVs in all zones."""
        return list(self._areas)

    def entities(self) -> list[str]:
        """Return all entities of the TRVs that should trigger a new evaluation when they change."""
        return [
            entity_id
            for zone in self._zones
            for trv in zone.trvs
            for entity_id in (trv.climate, trv.pi_heating_demand)
            if entity_id is not None
        ]

    async def async_added_to_hass(self, hass: HomeAssistant):
        self._hass = hass
        self.refresh()
        self.update()

    async def async_control_heating_loops(self, _time=None) -> None:
        """Asynchronously control heating loop for all areas."""
        for area in self._areas.values():
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

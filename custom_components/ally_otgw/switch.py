"""Switch platform: turns the control of the boiler on and off, while the TRVs decide what is needed."""
from __future__ import annotations

import logging
from datetime import timedelta, datetime
from time import monotonic
from typing import Optional, Callable, Any

from homeassistant.components import sensor, weather
from homeassistant.components.climate import HVACAction
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_ON, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall, Event, CoreState, EventStateChangedData, HassJob, callback
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval, async_call_later
from homeassistant.helpers.restore_state import RestoreEntity

from .area import Areas, SENSOR_TEMPERATURE_ID
from .const import *
from .const import PWMStatus
from .coordinator import SatDataUpdateCoordinator, DeviceState
from .entity import SatEntity
from .errors import Errors, Error
from .helpers import convert_time_str_to_seconds
from .manufacturers.geminox import Geminox
from .pwm import PWMState
from .relative_modulation import RelativeModulation, RelativeModulationState
from .util import create_pid_controller, create_heating_curve_controller, create_pwm_controller, create_minimum_setpoint_controller

ATTR_SETPOINT = "setpoint"
ATTR_OPTIMAL_COEFFICIENT = "optimal_coefficient"
ATTR_COEFFICIENT_DERIVATIVE = "coefficient_derivative"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_devices: AddEntitiesCallback):
    """Set up the heating switch, created together with the coordinator."""
    _async_add_devices([_hass.data[DOMAIN][_config_entry.entry_id][CLIMATE]])


class SatHeating(SatEntity, SwitchEntity, RestoreEntity):
    """Controls the boiler for the zones of Danfoss Ally TRVs, the TRVs themselves are the thermostats."""

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator, config_entry)

        # Get outside sensor entity IDs
        self.outside_sensor_entities = config_entry.data.get(CONF_OUTSIDE_SENSOR_ENTITY_ID)

        # If outside sensor entity IDs is a string, make it a list
        if isinstance(self.outside_sensor_entities, str):
            self.outside_sensor_entities = [self.outside_sensor_entities]

        # Create config options dictionary with defaults
        config_options = OPTIONS_DEFAULTS.copy()
        config_options.update(config_entry.options)

        self._alpha = 0.2
        self._sensors = []
        self._is_on = False
        self._setpoint = None
        self._calculated_setpoint = None
        self._last_boiler_temperature = None
        self._heat_allowed = True
        self._demand_lost_at = None
        self._error_source = None
        self._failsafe_active = False
        self._frost_guard_active = False

        self._control_heating_loop_unsub: Optional[Callable[[], None]] = None
        self._zones_unsub: Optional[Callable[[], None]] = None

        # System Configuration
        self._attr_name = str(config_entry.data.get(CONF_NAME))
        self._attr_id = str(config_entry.data.get(CONF_NAME)).lower()

        self._simulation = bool(config_entry.data.get(CONF_SIMULATION))
        self._heating_system = str(config_entry.data.get(CONF_HEATING_SYSTEM))
        self._overshoot_protection = bool(config_entry.data.get(CONF_OVERSHOOT_PROTECTION))

        # User Configuration
        self._dynamic_minimum_setpoint = bool(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT))
        self._maximum_relative_modulation = int(config_options.get(CONF_MAXIMUM_RELATIVE_MODULATION))
        self._minimum_setpoint_version = int(config_options.get(CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION))
        self._force_pulse_width_modulation = bool(config_options.get(CONF_FORCE_PULSE_WIDTH_MODULATION))
        self._sensor_max_value_age = convert_time_str_to_seconds(config_options.get(CONF_SENSOR_MAX_VALUE_AGE))

        # Create a PID controller with given configuration options
        self.pid = create_pid_controller(config_options)

        # Create Area controllers
        self.areas = Areas(config_entry.data, config_options)

        # Create Relative Modulation controller
        self.relative_modulation = RelativeModulation(coordinator, self._heating_system)

        # Create a Heating Curve controller with given configuration options
        self.heating_curve = create_heating_curve_controller(config_entry.data, config_options)

        # Create the Minimum Setpoint controller
        self.minimum_setpoint = create_minimum_setpoint_controller(config_entry.data, config_options)

        # Create a PWM controller with given configuration options
        self.pwm = create_pwm_controller(self.heating_curve, coordinator.supports_relative_modulation_management, config_entry.data, config_options)

        if self._simulation:
            _LOGGER.warning("Simulation mode!")

    def async_track_coordinator_data(self):
        """Track changes in the coordinator's boiler temperature and trigger the heating loop."""
        if self._coordinator.boiler_temperature is not None and self._last_boiler_temperature == self._coordinator.boiler_temperature:
            return

        self.schedule_control_heating_loop()
        self._last_boiler_temperature = self._coordinator.boiler_temperature

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # Restore the previous state if available, or set default values
        await self._restore_previous_state_or_set_defaults()

        # Discover the TRVs of our zones, before we control anything
        await self.areas.async_added_to_hass(self.hass)
        self._async_track_zones()

        # Follow TRVs that are added to, moved between or removed from our zones
        self.async_on_remove(self._async_untrack_zones)
        self.async_on_remove(self.hass.bus.async_listen(device_registry.EVENT_DEVICE_REGISTRY_UPDATED, self._async_registry_updated))
        self.async_on_remove(self.hass.bus.async_listen(entity_registry.EVENT_ENTITY_REGISTRY_UPDATED, self._async_registry_updated))

        # Update the heating curves if outside temperature is available
        if self.current_outside_temperature is not None:
            self.areas.heating_curves.update(self.current_outside_temperature)

        if self.hass.state is not CoreState.running:
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._register_event_listeners)
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self.async_control_heating_loop)
        else:
            await self._register_event_listeners()
            await self.async_control_heating_loop()

        # Register services
        await self._register_services()

        # Let the coordinator know we are ready
        await self._coordinator.async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Make sure a scheduled run of the control loop doesn't use the connection after we are gone."""
        if self._control_heating_loop_unsub is not None:
            self._control_heating_loop_unsub()
            self._control_heating_loop_unsub = None

        await super().async_will_remove_from_hass()

    async def _register_event_listeners(self, _time: Optional[datetime] = None):
        """Register event listeners."""
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self.schedule_control_heating_loop, timedelta(seconds=30)
            )
        )

        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_track_coordinator_data)
        )

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self.outside_sensor_entities, self._async_outside_entity_changed
            )
        )

    @callback
    def _async_track_zones(self) -> None:
        """Follow the entities of the TRVs that are currently in our zones."""
        self._async_untrack_zones()

        climates = self.areas.items()
        unsubscribes = [
            async_track_state_change_event(self.hass, climates, self._async_climate_changed),
            async_track_state_change_event(self.hass, [entity_id for entity_id in self.areas.entities() if entity_id not in climates], self._async_zone_entity_changed),
        ]

        self._zones_unsub = lambda: [unsubscribe() for unsubscribe in unsubscribes]

        for entity_id in climates:
            state = self.hass.states.get(entity_id)
            if state is not None and (sensor_temperature_id := state.attributes.get(SENSOR_TEMPERATURE_ID)):
                self.async_track_sensor_temperature(sensor_temperature_id)

    @callback
    def _async_untrack_zones(self) -> None:
        if self._zones_unsub is not None:
            self._zones_unsub()
            self._zones_unsub = None

    @callback
    def _async_registry_updated(self, _event: Event) -> None:
        """Discover the TRVs of our zones again, when devices or entities changed."""
        if self.areas.refresh():
            self._async_track_zones()
            self.areas.update()
            self.schedule_control_heating_loop()

    async def _async_zone_entity_changed(self, _event: Event[EventStateChangedData]) -> None:
        """Handle changes to the other entities of our TRVs, like how far their valves are open."""
        self.schedule_control_heating_loop()

    async def _restore_previous_state_or_set_defaults(self):
        """Restore the previous state if available or set default values."""
        old_state = await self.async_get_last_state()

        if old_state is not None:
            self.pwm.restore(old_state)
            self.pid.restore(old_state)

            self._is_on = old_state.state == STATE_ON

            if old_state.attributes.get(ATTR_SETPOINT):
                self._setpoint = old_state.attributes.get(ATTR_SETPOINT)

            if old_state.attributes.get(ATTR_OPTIMAL_COEFFICIENT):
                self.heating_curve.restore_autotune(
                    old_state.attributes.get(ATTR_OPTIMAL_COEFFICIENT),
                    old_state.attributes.get(ATTR_COEFFICIENT_DERIVATIVE)
                )

        self.async_write_ha_state()

    async def _register_services(self):
        async def reset_integral(_call: ServiceCall):
            """Service to reset the integral part of the PID controller."""
            self.pid.reset()
            self.areas.pids.reset()

        self.hass.services.async_register(DOMAIN, SERVICE_RESET_INTEGRAL, reset_integral)

    @property
    def name(self):
        """Return the friendly name of the switch."""
        return self._attr_name

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return self._attr_id

    @property
    def icon(self) -> str:
        return "mdi:radiator" if self.is_on else "mdi:radiator-off"

    @property
    def is_on(self) -> bool:
        """Return True when we control the boiler for our zones."""
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device state attributes."""
        return {
            "error": self.max_error.value,
            "error_pid": self.pid.last_error,
            "error_source": self.max_error.entity_id,
            "target_temperature": self.target_temperature,

            "integral": self.pid.integral,
            "derivative": self.pid.derivative,
            "proportional": self.pid.proportional,
            "integral_enabled": self.pid.integral_enabled,

            "boiler_flame_timing": self._coordinator.flame.average_on_time_seconds,
            "boiler_temperature_cold": self._coordinator.boiler_temperature_cold,
            "boiler_temperature_tracking": self._coordinator.boiler_temperature_tracking,
            "boiler_temperature_derivative": self._coordinator.boiler_temperature_derivative,

            "derivative_enabled": self.pid.derivative_enabled,
            "derivative_raw": self.pid.raw_derivative,

            "current_kp": self.pid.kp,
            "current_ki": self.pid.ki,
            "current_kd": self.pid.kd,

            "setpoint": self._setpoint,
            "heating_curve": self.heating_curve.value,
            "requested_setpoint": self.requested_setpoint,
            "minimum_setpoint": self.minimum_setpoint_value,
            "outside_temperature": self.current_outside_temperature,
            "optimal_coefficient": self.heating_curve.optimal_coefficient,
            "coefficient_derivative": self.heating_curve.coefficient_derivative,

            "relative_modulation_value": self.relative_modulation_value,
            "relative_modulation_enabled": self.relative_modulation.enabled,
            "relative_modulation_state": self.relative_modulation_state.name,

            "zones": self.areas.summary(),
            "heat_demand": self.heat_demand,
            "heat_allowed": self._heat_allowed,
            "frost_guard": self._frost_guard_active,
            "failsafe": self._failsafe_active,

            "pulse_width_modulation_enabled": self.pwm.enabled,
            "pulse_width_modulation_active": self.pulse_width_modulation_enabled,
            "pulse_width_modulation_state": self.pwm.status.name,
            "pulse_width_modulation_duty_cycle": self.pwm.duty_cycle,
        }

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we heat for: the target of the TRV that drives the boiler, or the frost guard."""
        if self._frost_guard_active:
            return FROST_GUARD_TARGET_TEMPERATURE

        if (target_temperature := self.areas.target_temperature(self.max_error.entity_id)) is not None:
            return target_temperature

        # Nobody is calling for heat, keep the heating curve ready for the warmest room
        return self.areas.highest_target_temperature()

    @property
    def current_outside_temperature(self):
        """Return the current outside temperature"""
        self.outside_sensor_entities.sort(key=lambda x: "sensor" not in x)
        for entity_id in self.outside_sensor_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
                continue

            if sensor.DOMAIN in entity_id:
                return float(state.state)

            if weather.DOMAIN in entity_id:
                return float(state.attributes.get("temperature"))

        return None

    @property
    def hvac_action(self) -> HVACAction:
        """Get the current HVAC action."""
        if not self._is_on:
            return HVACAction.OFF

        if not self._coordinator.device_active:
            return HVACAction.IDLE

        return HVACAction.HEATING

    @property
    def errors(self) -> Errors:
        """Return the errors of the TRVs in the zones that are calling for heat."""
        return self.areas.errors

    @property
    def max_error(self) -> Error:
        """Return the largest error, or a neutral one when no TRV is calling for heat."""
        if len(errors := self.errors) == 0:
            return Error(self.entity_id or DOMAIN, 0.0)

        return max(errors, key=lambda error: error.value)

    @property
    def heat_demand(self) -> bool:
        """Return True when a zone is calling for heat, or when the frost guard needs heat."""
        if (demand := self.areas.demand) is None:
            return self._frost_guard_active

        return demand

    @property
    def setpoint(self) -> float | None:
        return self._setpoint

    @property
    def requested_setpoint(self) -> float:
        """Get the requested setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        return round(max(self.heating_curve.value + self.pid.output, MINIMUM_SETPOINT), 1)

    @property
    def pulse_width_modulation_enabled(self) -> bool:
        """Return True if pulse width modulation is enabled, False otherwise."""
        # Nothing may heat, so there is nothing to modulate either
        if not self._heat_allowed:
            return False

        if not self._coordinator.supports_setpoint_management or self._force_pulse_width_modulation:
            return True

        if not self._overshoot_protection or self._calculated_setpoint is None:
            return False

        if not self._dynamic_minimum_setpoint:
            return self._coordinator.minimum_setpoint > self._calculated_setpoint

        if self._minimum_setpoint_version == 1:
            return self.minimum_setpoint.current > self._calculated_setpoint

        return self.pwm.enabled

    @property
    def relative_modulation_value(self) -> int:
        if not self.relative_modulation.enabled and self._coordinator.supports_relative_modulation_management:
            return MINIMUM_RELATIVE_MODULATION

        return self._maximum_relative_modulation

    @property
    def relative_modulation_state(self) -> RelativeModulationState:
        return self.relative_modulation.state

    @property
    def minimum_setpoint_value(self) -> float:
        """Get the minimum allowable setpoint temperature."""
        if self._dynamic_minimum_setpoint:
            # Version 1: Use MinimumSetpoint controller
            if self._minimum_setpoint_version == 1:
                return max(self.minimum_setpoint.current, self._coordinator.minimum_setpoint)

            # Version 2: Use Pulse Width Modulation controller
            if self._minimum_setpoint_version == 2:
                return max(self.pwm.setpoint, self._coordinator.minimum_setpoint)

        # Default to coordinator's minimum setpoint
        return self._coordinator.minimum_setpoint

    def _calculate_control_setpoint(self) -> float:
        """Calculate the control setpoint based on the heating curve and PID output."""
        if self.heating_curve.value is None:
            return MINIMUM_SETPOINT

        return self.requested_setpoint

    async def _async_outside_entity_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the outside entity."""
        if event.data.get("new_state") is None:
            return

        _LOGGER.debug("Outside Sensor Changed.")
        self.async_write_ha_state()

        self._async_control_pid()
        self.schedule_control_heating_loop()

    async def _async_climate_changed(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the climate entity of a TRV.
        If the state, target temperature, or current temperature of the climate
        entity has changed, update the PID controller and heating control.
        """
        # Get the new state of the climate entity
        new_state = event.data.get("new_state")

        # Return if the new state is not available
        if not new_state:
            return

        # Get the old state of the climate entity
        old_state = event.data.get("old_state")

        # Get the attributes of the new state
        new_attrs = new_state.attributes

        # Get the attributes of the old state, if available
        old_attrs = old_state.attributes if old_state else {}

        _LOGGER.debug(f"Climate State Changed ({new_state.entity_id}).")

        # Check if the last state is None, so we can track the attached sensor if needed
        if old_state is None and (sensor_temperature_id := new_attrs.get(SENSOR_TEMPERATURE_ID)):
            self.async_track_sensor_temperature(sensor_temperature_id)

        # If the state has changed or the old state is not available, update the PID controller
        if not old_state or new_state.state != old_state.state:
            self._async_control_pid(True)

        # If the target temperature has changed, update the PID controller
        elif new_attrs.get("temperature") != old_attrs.get("temperature"):
            self._async_control_pid(True)

        # If the current temperature has changed, update the PID controller
        elif SENSOR_TEMPERATURE_ID not in new_attrs and new_attrs.get("current_temperature") != old_attrs.get("current_temperature"):
            self._async_control_pid()

        # If the TRV started or stopped calling for heat, its error changed as well
        elif new_attrs.get("hvac_action") != old_attrs.get("hvac_action"):
            self._async_control_pid()

        # Update the heating control
        self.schedule_control_heating_loop()

    async def _async_temperature_change(self, event: Event[EventStateChangedData]) -> None:
        """Handle changes to the climate sensor entity."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return

        _LOGGER.debug(f"Climate Sensor Changed ({new_state.entity_id}).")
        self._async_control_pid()
        self.schedule_control_heating_loop()

    def _async_control_pid(self, reset: bool = False) -> None:
        """Control the PID controller."""
        # We can't continue if we don't have a valid outside temperature
        if self.current_outside_temperature is None:
            return

        # Reset the PID controller if the sensor data is too old
        if self._sensor_max_value_age != 0 and monotonic() - self.pid.last_updated > self._sensor_max_value_age:
            self.pid.reset()
            self.areas.pids.reset()

        # Calculate the maximum error between the current temperature and the target temperature of all climates
        self.areas.update()
        max_error = self.max_error
        target_temperature = self.target_temperature

        # Make sure we use the latest heating curve value
        self.areas.heating_curves.update(self.current_outside_temperature)
        if target_temperature is not None:
            self.heating_curve.update(target_temperature, self.current_outside_temperature)

        # Restart the PID from the new error when another room takes over, so the jump between them doesn't kick the derivative
        if not reset and self._error_source is not None and max_error.entity_id != self._error_source:
            _LOGGER.info(f"Error source changed from {self._error_source} to {max_error.entity_id}, resetting the PID controller")
            self.pid.update_reset(error=max_error, heating_curve_value=self.heating_curve.value)

        self._error_source = max_error.entity_id

        # Update the PID controller with the maximum error
        if not reset:
            _LOGGER.info(f"Updating error to {max_error.value} from {max_error.entity_id} (Reset: False)")

            # Calculate an optimal heating curve when we are in the deadband of a real room
            if target_temperature is not None and not self._frost_guard_active and -DEADBAND <= max_error.value <= DEADBAND:
                self.heating_curve.autotune(
                    setpoint=self.requested_setpoint,
                    target_temperature=target_temperature,
                    outside_temperature=self.current_outside_temperature
                )

            # Update our PID controllers if we have valid values
            if self._coordinator.boiler_temperature_filtered is not None:
                self.areas.pids.update(self._coordinator.boiler_temperature_filtered)

                if self.heating_curve.value is not None:
                    self.pid.update(max_error, self.heating_curve.value, self._coordinator.boiler_temperature_filtered)

        elif max_error.value != self.pid.last_error:
            _LOGGER.info(f"Updating error to {max_error.value} from {max_error.entity_id} (Reset: True)")

            self.pid.update_reset(error=max_error, heating_curve_value=self.heating_curve.value)
            self._calculated_setpoint = None
            self.pwm.reset()

        self.async_write_ha_state()

    async def _async_control_setpoint(self, pwm_state: PWMState) -> None:
        """Control the setpoint of the heating system based on the current mode and PWM state."""
        if not self._heat_allowed:
            # None of the rooms is calling for heat, so neither the heating curve nor PWM may fire the boiler
            self._setpoint = MINIMUM_SETPOINT
            _LOGGER.info("No room is calling for heat. Setting setpoint to minimum: %.1f°C", MINIMUM_SETPOINT)

        elif not self.pulse_width_modulation_enabled or pwm_state.status == PWMStatus.IDLE:
            # Normal cycle without PWM
            self._setpoint = self._calculated_setpoint
            _LOGGER.info("Pulse Width Modulation is disabled or in IDLE state. Running normal heating cycle.")
            _LOGGER.debug("Calculated setpoint for normal cycle: %.1f°C", self._calculated_setpoint)

            # Some final checks to see if it's even warm
            if self._setpoint < COLD_SETPOINT:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug("Calculated setpoint is too cold. Setting setpoint to minimum: %.1f°C", MINIMUM_SETPOINT)
        else:
            # PWM is enabled and actively controlling the cycle
            _LOGGER.info("Running PWM cycle with state: %s", pwm_state.status)

            if pwm_state.status == PWMStatus.ON:
                self._setpoint = self.minimum_setpoint_value
                _LOGGER.debug("Setting setpoint to minimum: %.1f°C", self._setpoint)
            else:
                self._setpoint = MINIMUM_SETPOINT
                _LOGGER.debug("Setting setpoint to absolute minimum: %.1f°C", MINIMUM_SETPOINT)

        # Apply the setpoint using the coordinator
        await self._coordinator.async_set_control_setpoint(min(self._setpoint, self._coordinator.maximum_setpoint))
        _LOGGER.info("Control setpoint has been updated to: %.1f°C", self._setpoint)

    async def _async_control_relative_modulation(self) -> None:
        """Control the relative modulation value based on the conditions."""
        if not self._coordinator.supports_relative_modulation_management:
            _LOGGER.debug("Relative modulation management is not supported. Skipping control.")
            return

        # Update relative modulation state
        await self.relative_modulation.update(self.pulse_width_modulation_enabled)

        # Retrieve the relative modulation
        relative_modulation_value = self.relative_modulation_value

        # Apply some filters based on the manufacturer
        if isinstance(self._coordinator.manufacturer, Geminox):
            relative_modulation_value = max(10, relative_modulation_value)

        # Determine if the value needs to be updated
        if self._coordinator.maximum_relative_modulation_value == relative_modulation_value:
            _LOGGER.debug("Relative modulation value unchanged (%d%%). No update necessary.", relative_modulation_value)
            return

        await self._coordinator.async_set_control_max_relative_modulation(relative_modulation_value)

    def _update_frost_guard(self) -> None:
        """Keep the house from freezing when none of the TRVs can tell us anything anymore."""
        outside_temperature = self.current_outside_temperature
        active = self.areas.demand is None and outside_temperature is not None and (
                outside_temperature < FROST_GUARD_OUTSIDE_TEMPERATURE or
                (self._frost_guard_active and outside_temperature < FROST_GUARD_OUTSIDE_TEMPERATURE + FROST_GUARD_HYSTERESIS)
        )

        if active == self._frost_guard_active:
            return

        self._frost_guard_active = active

        if active:
            _LOGGER.warning("None of the TRVs is available and it's %.1f°C outside, heating for %.0f°C to protect against frost.", outside_temperature, FROST_GUARD_TARGET_TEMPERATURE)
        else:
            _LOGGER.warning("Frost guard is no longer needed.")

        # Follow the frost guard target, or the TRVs again
        self._async_control_pid(True)

    def _update_heat_allowed(self) -> None:
        """Allow heating while a room is calling for heat, and let a running burn finish once none of them is."""
        if self.heat_demand:
            # Start from what is requested now, not from the setpoint we smoothed while nobody wanted heat
            if not self._heat_allowed:
                self._calculated_setpoint = None

            self._heat_allowed = True
            self._demand_lost_at = None
            return

        if self._demand_lost_at is None:
            self._demand_lost_at = monotonic()

            # Only a burn that is already running may finish, hot water is not ours to finish
            self._heat_allowed = self._coordinator.flame_active and not self._coordinator.hot_water_active
            _LOGGER.info("No room is calling for heat anymore (%s).", "finishing the current burn" if self._heat_allowed else "stopping")
            return

        if not self._heat_allowed:
            return

        if not self._coordinator.flame_active or monotonic() - self._demand_lost_at > DEMAND_BURN_FINISH_TIMEOUT:
            self._heat_allowed = False
            _LOGGER.info("Finished the burn without any room calling for heat, stopping.")

    async def _async_control_failsafe(self) -> None:
        """Turn the heater off, because we are missing the outside temperature to control it."""
        if not self._failsafe_active:
            _LOGGER.warning("Missing the outside temperature, turning the heater off.")
            self._failsafe_active = True

        self.pwm.reset()
        self._calculated_setpoint = None
        self._setpoint = MINIMUM_SETPOINT

        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
        await self.async_set_heater_state(DeviceState.OFF)

        self.async_write_ha_state()

    def reset_control_state(self):
        """Reset control state when major changes occur."""
        self.pwm.disable()
        self._calculated_setpoint = None

    def async_track_sensor_temperature(self, entity_id):
        """
        Track the temperature of the sensor specified by the given entity_id.

        Parameters:
        entity_id (str): The entity id of the sensor to track.

        If the sensor is already being tracked, the method will return without doing anything.
        Otherwise, it will register a callback for state changes on the specified sensor and start tracking its temperature.
        """
        if entity_id in self._sensors:
            return

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [entity_id], self._async_temperature_change
            )
        )

        self._sensors.append(entity_id)

    def schedule_control_heating_loop(self, _time: Optional[datetime] = None, force: bool = False, ) -> None:
        """Schedule a debounced execution of the heating control loop."""
        # Force immediate execution
        if force:
            # Cancel previous scheduled run, if any
            if self._control_heating_loop_unsub is not None:
                self._control_heating_loop_unsub()
                self._control_heating_loop_unsub = None

                self.hass.async_create_task(self.async_control_heating_loop())
                return

        # If a run is already scheduled, do nothing.
        if self._control_heating_loop_unsub is not None:
            return

        self._control_heating_loop_unsub = async_call_later(self.hass, 10, HassJob(self.async_control_heating_loop))

    async def async_control_heating_loop(self, _time: Optional[datetime] = None) -> None:
        """Control the heating based on what the TRVs ask for and the outside temperature."""
        # Let the sub know we have run
        self._control_heating_loop_unsub = None

        # No need to do anything if we are not on
        if not self._is_on:
            return

        # Determine which zones are calling for heat
        self.areas.update()

        # Without the outside temperature, make sure the boiler doesn't keep heating on an old setpoint
        if self.current_outside_temperature is None:
            await self._async_control_failsafe()
            return

        if self._failsafe_active:
            _LOGGER.warning("The outside temperature is available again, resuming control.")
            self._failsafe_active = False

        # Protect against frost when none of the TRVs can tell us anything
        self._update_frost_guard()

        # Make sure the heating curve follows the latest target and outside temperature
        self.areas.heating_curves.update(self.current_outside_temperature)
        if (target_temperature := self.target_temperature) is not None:
            self.heating_curve.update(target_temperature, self.current_outside_temperature)

        # Control the heating through the coordinator
        await self._coordinator.async_control_heating_loop(climate=self, pwm_state=self.pwm.state)

        # Determine if we are allowed to heat, now that we know the latest flame state
        self._update_heat_allowed()

        if self._calculated_setpoint is None:
            # Default to the calculated setpoint
            self._calculated_setpoint = self._calculate_control_setpoint()
        else:
            # Apply low filter on the requested setpoint
            self._calculated_setpoint = round(self._alpha * self._calculate_control_setpoint() + (1 - self._alpha) * self._calculated_setpoint, 1)

        # Check for overshoot
        if self._coordinator.device_status == BoilerStatus.OVERSHOOT_HANDLING:
            _LOGGER.info("Overshoot Handling detected, enabling Pulse Width Modulation.")
            self.pwm.enable()

        # Pulse Width Modulation
        if not self.pulse_width_modulation_enabled:
            self.pwm.reset()
        else:
            await self.pwm.update(flame=self._coordinator.flame, boiler=self._coordinator.boiler, requested_setpoint=self._calculated_setpoint)

        # Set the control setpoint to make sure we always stay in control
        await self._async_control_setpoint(self.pwm.state)

        # Set the relative modulation value, if supported
        await self._async_control_relative_modulation()

        # Control the integral (if exceeded the time limit)
        if self.heating_curve.value is not None:
            self.pid.update_integral(self.max_error, self.heating_curve.value)

        # Control our areas
        await self.areas.async_control_heating_loops()

        # Control our dynamic minimum setpoint (version 1)
        if not self._coordinator.hot_water_active and self._coordinator.flame_active:
            # Calculate the base return temperature
            if self._coordinator.device_status == BoilerStatus.HEATING_UP:
                self.minimum_setpoint.warming_up(self._coordinator.boiler)

            # Calculate the dynamic minimum setpoint
            self.minimum_setpoint.calculate(self._coordinator.boiler, self.pwm.status)

        # If the setpoint is high, turn on the heater
        await self.async_set_heater_state(DeviceState.ON if self._setpoint is not None and self._setpoint > COLD_SETPOINT else DeviceState.OFF)

        self.async_write_ha_state()

    async def async_set_heater_state(self, state: DeviceState):
        """Set the heater state, ensuring proper conditions are met."""
        _LOGGER.debug("Attempting to set heater state to: %s", state)

        if state == DeviceState.ON and self._coordinator.device_active:
            _LOGGER.info("Heater is already active. No action taken.")
            return

        if state == DeviceState.OFF and not self._coordinator.device_active:
            _LOGGER.info("Heater is already off. No action taken.")
            return

        await self._coordinator.async_set_heater_state(state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start controlling the boiler for our zones."""
        await self._async_set_on(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop controlling the boiler, and make sure it doesn't heat anymore."""
        await self._async_set_on(False)

        self._setpoint = MINIMUM_SETPOINT
        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
        await self.async_set_heater_state(DeviceState.OFF)

    async def _async_set_on(self, is_on: bool) -> None:
        self._is_on = is_on

        # Reset the PID controller
        self._async_control_pid(True)

        # Reset the control state
        self.reset_control_state()

        # Update the state and control the heating
        self.async_write_ha_state()
        self.schedule_control_heating_loop()

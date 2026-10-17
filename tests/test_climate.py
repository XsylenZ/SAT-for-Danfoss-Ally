"""The tests for the climate component."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.climate import SatClimate
from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.coordinator import DeviceState
from custom_components.ally_otgw.fake import SatFakeCoordinator
from tests.helpers import add_trv


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_1(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 57
    assert climate.heating_curve.value == 32.2

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 23.83
    assert climate.pwm.duty_cycle == (285, 914)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 58,
                CONF_MAXIMUM_SETPOINT: 75
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.3,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 18.99 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 11.1 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_2(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(58)
    await climate.async_set_target_temperature(19.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 10
    assert climate.heating_curve.value == 27.8
    assert climate.requested_setpoint == 28.0

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 2.6
    assert climate.pwm.duty_cycle == (0, 2400)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 41,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 0.9,
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 19.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ -2.2 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_scenario_3(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(41)
    await climate.async_set_target_temperature(20.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    climate.schedule_control_heating_loop(force=True)

    assert climate.setpoint == 41.0
    assert climate.heating_curve.value == 32.5
    assert climate.requested_setpoint == 34.6

    assert climate.pulse_width_modulation_enabled
    assert climate.pwm.last_duty_cycle_percentage == 53.62
    assert climate.pwm.duty_cycle == (643, 556)


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}],
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_no_heat_without_room_demand(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    async def set_room(hvac_action: str) -> None:
        hass.states.async_set("climate.radiator_1", HVACMode.HEAT, {"current_temperature": 19.0, "temperature": 20.0, "hvac_action": hvac_action, "hvac_modes": [HVACMode.HEAT]})
        await climate.async_control_heating_loop()

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    # The main room and the heating curve ask for heat, but the only room is idle
    await set_room("idle")
    assert not climate.heat_demand
    assert not climate.pulse_width_modulation_enabled
    assert not climate.extra_state_attributes["pulse_width_modulation_active"]
    assert climate.pwm.status == PWMStatus.IDLE
    assert climate.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # As soon as the room calls for heat, the boiler is allowed to fire again
    await set_room("heating")
    assert climate.heat_demand
    assert climate.pulse_width_modulation_enabled
    assert climate.setpoint > COLD_SETPOINT
    assert coordinator.flame_active

    # The room is satisfied while the burner is running, so the burn may finish
    await set_room("idle")
    assert not climate.heat_demand
    assert climate.setpoint > COLD_SETPOINT
    assert coordinator.flame_active

    # The burn has finished, so we stop and don't start again
    await coordinator.async_set_heater_state(DeviceState.OFF)
    await climate.async_control_heating_loop()
    assert climate.setpoint == MINIMUM_SETPOINT

    await climate.async_control_heating_loop()
    assert climate.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # A burn that keeps going is stopped after the timeout
    await set_room("heating")
    await set_room("idle")
    assert climate.setpoint > COLD_SETPOINT

    climate._demand_lost_at -= DEMAND_BURN_FINISH_TIMEOUT + 1
    await climate.async_control_heating_loop()
    assert climate.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}],
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 24.8 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_error_source_change_does_not_kick_derivative(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    def set_room(current: float, hvac_action: str) -> None:
        hass.states.async_set("climate.radiator_1", HVACMode.HEAT, {"current_temperature": current, "temperature": 20.0, "hvac_action": hvac_action, "hvac_modes": [HVACMode.HEAT]})

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    # The main room is well above its target and the room is idle
    set_room(20.6, "idle")
    climate._async_control_pid()
    assert climate.max_error.entity_id == climate.entity_id
    assert climate.max_error.value == -3.8

    # The room takes over, its error is close to zero, far from the main room
    set_room(20.6, "heating")
    climate.pid._last_updated -= 165
    climate._async_control_pid()

    assert climate.max_error.entity_id == "climate.radiator_1"
    assert climate.pid.last_error == climate.max_error.value
    assert climate.pid.raw_derivative == 0
    assert climate.requested_setpoint < 50


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}],
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 24.8 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_stale_room_is_ignored_until_it_reports_again(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    from datetime import timedelta
    from homeassistant.util import dt

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)

    # The room froze while calling for heat in a cold room, so it is ignored and the boiler stays off
    stale = dt.utcnow() - timedelta(seconds=AREA_STATE_MAX_AGE + 60)
    attributes = {"current_temperature": 18.0, "temperature": 20.0, "hvac_action": "heating", "hvac_modes": [HVACMode.HEAT]}
    hass.states.async_set("climate.radiator_1", HVACMode.HEAT, attributes, timestamp=stale.timestamp())
    climate._async_control_pid()
    await climate.async_control_heating_loop()

    assert not climate.heat_demand
    assert climate.max_error.entity_id == climate.entity_id
    assert climate.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # With every room out, the main room only asks for heat when it is below its own target
    await climate.async_set_target_temperature(26.0)
    await climate.async_control_heating_loop()
    assert climate.heat_demand

    await climate.async_set_target_temperature(21.0)
    await coordinator.async_set_heater_state(DeviceState.OFF)
    climate._demand_lost_at = None
    await climate.async_control_heating_loop()
    assert not climate.heat_demand
    assert climate.setpoint == MINIMUM_SETPOINT

    # The room reports again and takes over, without kicking the derivative
    hass.states.async_set("climate.radiator_1", HVACMode.HEAT, attributes)
    climate.pid._last_updated -= 165
    climate._async_control_pid()
    await climate.async_control_heating_loop()

    assert climate.heat_demand
    assert climate._heat_allowed
    assert climate.max_error.entity_id == "climate.radiator_1"
    assert climate.pid.raw_derivative == 0
    assert climate.requested_setpoint > COLD_SETPOINT


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
                CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}],
                CONF_FORCE_PULSE_WIDTH_MODULATION: True,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 20.9 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_failsafe_without_inside_temperature(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    room = {"current_temperature": 19.0, "temperature": 20.0, "hvac_action": "heating", "hvac_modes": [HVACMode.HEAT]}
    hass.states.async_set("climate.radiator_1", HVACMode.HEAT, room)

    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    assert coordinator.flame_active

    # The inside sensor is gone, but the room still tells us it needs heat, so we keep heating for it
    hass.states.async_set("sensor.test_inside_sensor", STATE_UNAVAILABLE)
    await climate.async_control_heating_loop()
    assert not climate.extra_state_attributes["failsafe"]
    assert climate.heat_demand
    assert coordinator.flame_active

    # The room is gone as well, so nothing can tell us anything: turn the heater off, don't keep the old setpoint
    hass.states.async_set("climate.radiator_1", STATE_UNAVAILABLE, room)
    await climate.async_control_heating_loop()
    assert climate.extra_state_attributes["failsafe"]
    assert climate.setpoint == MINIMUM_SETPOINT
    assert coordinator.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # Both are back, so we resume
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("climate.radiator_1", HVACMode.HEAT, room)
    await climate.async_control_heating_loop()
    assert not climate.extra_state_attributes["failsafe"]
    assert climate.heat_demand


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
                CONF_MINIMUM_SETPOINT: 57,
                CONF_MAXIMUM_SETPOINT: 75,
            },
            {
                CONF_HEATING_CURVE_COEFFICIENT: 1.8,
            },
            {
                TEMPLATE_DOMAIN: [
                    {
                        SENSOR_DOMAIN: [
                            {
                                "name": "test_inside_sensor",
                                "state": "{{ 18.0 | float }}",
                            },
                            {
                                "name": "test_outside_sensor",
                                "state": "{{ 9.9 | float }}",
                            }
                        ]
                    },
                ],
            },
    )],
])
async def test_failsafe_without_rooms(hass: HomeAssistant, entry: MockConfigEntry, climate: SatClimate, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(57)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    assert coordinator.flame_active

    # Without rooms, losing the outside sensor means we can't calculate anything anymore
    hass.states.async_set("sensor.test_outside_sensor", STATE_UNAVAILABLE)
    await climate.async_control_heating_loop()
    assert climate.extra_state_attributes["failsafe"]
    assert coordinator.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

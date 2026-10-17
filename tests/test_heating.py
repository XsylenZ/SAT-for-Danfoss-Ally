"""The tests for the heating switch, which controls the boiler for the zones of Danfoss Ally TRVs."""
from datetime import timedelta

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE, STATE_ON, STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.util import dt
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.coordinator import DeviceState
from custom_components.ally_otgw.fake import SatFakeCoordinator
from custom_components.ally_otgw.switch import SatHeating
from tests.helpers import add_trv

ZONE = [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}]


def setup(minimum_setpoint: float = 57, outside: float = 9.9, **options):
    """Return the parameters for the entry fixture: a fake boiler, one zone and an outside sensor."""
    return [(
        [(TEMPLATE_DOMAIN, 1)],
        {
            CONF_MODE: MODE_FAKE,
            CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
            CONF_MINIMUM_SETPOINT: minimum_setpoint,
            CONF_MAXIMUM_SETPOINT: 75,
        },
        {
            CONF_HEATING_CURVE_COEFFICIENT: 1.8,
            CONF_ZONES: ZONE,
            **options,
        },
        {
            TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": f"{{{{ {outside} | float }}}}"}]}],
        },
    )]


def set_trv(hass: HomeAssistant, current: float, target: float, hvac_action: str | None = None, **kwargs) -> None:
    attributes = {"current_temperature": current, "temperature": target, "hvac_modes": [HVACMode.HEAT]}
    if hvac_action is not None:
        attributes["hvac_action"] = hvac_action

    hass.states.async_set("climate.radiator_1", HVACMode.HEAT, attributes, **kwargs)


# The original scenarios of SAT: a room 0.1 below its target, now a TRV that doesn't report an action
@pytest.mark.parametrize("domains, data, options, config", setup(57, 9.9, **{CONF_FORCE_PULSE_WIDTH_MODULATION: True}))
async def test_scenario_1(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 20.9, 21.0)

    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    assert heating.target_temperature == 21.0
    assert heating.setpoint == 57
    assert heating.heating_curve.value == 32.2

    assert heating.pulse_width_modulation_enabled
    assert heating.pwm.last_duty_cycle_percentage == 23.83
    assert heating.pwm.duty_cycle == (285, 914)


@pytest.mark.parametrize("domains, data, options, config", setup(58, 11.1, **{CONF_HEATING_CURVE_COEFFICIENT: 1.3, CONF_FORCE_PULSE_WIDTH_MODULATION: True}))
async def test_scenario_2(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 18.99, 19.0)

    await coordinator.async_set_boiler_temperature(58)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    assert heating.setpoint == 10
    assert heating.heating_curve.value == 27.8
    assert heating.requested_setpoint == 28.0

    assert heating.pulse_width_modulation_enabled
    assert heating.pwm.last_duty_cycle_percentage == 2.6
    assert heating.pwm.duty_cycle == (0, 2400)


@pytest.mark.parametrize("domains, data, options, config", setup(41, -2.2, **{CONF_HEATING_CURVE_COEFFICIENT: 0.9, CONF_FORCE_PULSE_WIDTH_MODULATION: True}))
async def test_scenario_3(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 19.9, 20.0)

    await coordinator.async_set_boiler_temperature(41)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    assert heating.setpoint == 41.0
    assert heating.heating_curve.value == 32.5
    assert heating.requested_setpoint == 34.6

    assert heating.pulse_width_modulation_enabled
    assert heating.pwm.last_duty_cycle_percentage == 53.62
    assert heating.pwm.duty_cycle == (643, 556)


@pytest.mark.parametrize("domains, data, options, config", setup(57, 9.9, **{CONF_FORCE_PULSE_WIDTH_MODULATION: True}))
async def test_switch_turns_the_boiler_on_and_off(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 19.0, 20.0, "heating")
    await coordinator.async_set_boiler_temperature(57)

    # Off by default, so nothing is controlled
    assert hass.states.get(heating.entity_id).state == STATE_OFF
    await heating.async_control_heating_loop()
    assert not coordinator.flame_active

    await hass.services.async_call("switch", "turn_on", {"entity_id": heating.entity_id}, blocking=True)
    await heating.async_control_heating_loop()
    assert hass.states.get(heating.entity_id).state == STATE_ON
    assert coordinator.flame_active

    # Turning it off stops the boiler right away
    await hass.services.async_call("switch", "turn_off", {"entity_id": heating.entity_id}, blocking=True)
    assert hass.states.get(heating.entity_id).state == STATE_OFF
    assert coordinator.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active


@pytest.mark.parametrize("domains, data, options, config", setup(57, 9.9, **{CONF_FORCE_PULSE_WIDTH_MODULATION: True}))
async def test_no_heat_without_room_demand(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    async def set_room(hvac_action: str) -> None:
        set_trv(hass, 19.0, 20.0, hvac_action)
        await heating.async_control_heating_loop()

    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()

    # The room is idle, so the heating curve may not fire the boiler
    await set_room("idle")
    assert not heating.heat_demand
    assert not heating.pulse_width_modulation_enabled
    assert not heating.extra_state_attributes["pulse_width_modulation_active"]
    assert heating.pwm.status == PWMStatus.IDLE
    assert heating.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # As soon as the room calls for heat, the boiler is allowed to fire again
    await set_room("heating")
    assert heating.heat_demand
    assert heating.pulse_width_modulation_enabled
    assert heating.setpoint > COLD_SETPOINT
    assert coordinator.flame_active

    # The room is satisfied while the burner is running, so the burn may finish
    await set_room("idle")
    assert not heating.heat_demand
    assert heating.setpoint > COLD_SETPOINT
    assert coordinator.flame_active

    # The burn has finished, so we stop and don't start again
    await coordinator.async_set_heater_state(DeviceState.OFF)
    await heating.async_control_heating_loop()
    assert heating.setpoint == MINIMUM_SETPOINT

    await heating.async_control_heating_loop()
    assert heating.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # A burn that keeps going is stopped after the timeout
    await set_room("heating")
    await set_room("idle")
    assert heating.setpoint > COLD_SETPOINT

    heating._demand_lost_at -= DEMAND_BURN_FINISH_TIMEOUT + 1
    await heating.async_control_heating_loop()
    assert heating.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_error_source_change_does_not_kick_derivative(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()

    # Nobody calls for heat
    set_trv(hass, 20.6, 20.0, "idle")
    heating._async_control_pid()
    assert heating.max_error.entity_id == heating.entity_id
    assert heating.max_error.value == 0

    # The room takes over
    set_trv(hass, 18.0, 20.0, "heating")
    heating.pid._last_updated -= 165
    heating._async_control_pid()

    assert heating.max_error.entity_id == "climate.radiator_1"
    assert heating.pid.last_error == heating.max_error.value
    assert heating.pid.raw_derivative == 0


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_stale_room_is_ignored_until_it_reports_again(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()

    # The room froze while calling for heat in a cold room, so it is ignored and the boiler stays off
    stale = dt.utcnow() - timedelta(seconds=AREA_STATE_MAX_AGE + 60)
    set_trv(hass, 18.0, 20.0, "heating", timestamp=stale.timestamp())
    await heating.async_control_heating_loop()

    assert not heating.heat_demand
    assert heating.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # The room reports again and takes over, without kicking the derivative
    set_trv(hass, 18.0, 20.0, "heating")
    heating.pid._last_updated -= 165
    heating._async_control_pid()
    await heating.async_control_heating_loop()

    assert heating.heat_demand
    assert heating.max_error.entity_id == "climate.radiator_1"
    assert heating.pid.raw_derivative == 0
    assert heating.requested_setpoint > COLD_SETPOINT


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_failsafe_without_outside_temperature(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 19.0, 20.0, "heating")

    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()
    assert coordinator.flame_active

    # Without the outside temperature we can't calculate anything: turn the heater off, don't keep the old setpoint
    hass.states.async_set("sensor.test_outside_sensor", STATE_UNAVAILABLE)
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["failsafe"]
    assert heating.setpoint == MINIMUM_SETPOINT
    assert coordinator.setpoint == MINIMUM_SETPOINT
    assert not coordinator.flame_active

    # It is back, so we resume
    hass.states.async_set("sensor.test_outside_sensor", "9.9")
    await heating.async_control_heating_loop()
    assert not heating.extra_state_attributes["failsafe"]
    assert coordinator.flame_active


@pytest.mark.parametrize("domains, data, options, config", setup(48, -15.0, **{CONF_HEATING_CURVE_COEFFICIENT: 2.0}))
async def test_frost_guard_without_any_available_trv(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 19.0, 20.0, "heating")

    await coordinator.async_set_boiler_temperature(48)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()
    assert not heating.extra_state_attributes["frost_guard"]

    # Nothing can tell us how warm it is inside, and it freezes outside: heat as if the house wants 10°C
    hass.states.async_set("climate.radiator_1", STATE_UNAVAILABLE)
    await heating.async_control_heating_loop()

    assert heating.extra_state_attributes["frost_guard"]
    assert heating.heat_demand
    assert heating.target_temperature == FROST_GUARD_TARGET_TEMPERATURE
    assert heating.heating_curve.value == 32.6
    assert heating.requested_setpoint == 32.6

    # Warmer outside, but within the hysteresis, it keeps guarding
    hass.states.async_set("sensor.test_outside_sensor", str(FROST_GUARD_OUTSIDE_TEMPERATURE + 0.5))
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["frost_guard"]

    # Above it, the frost guard stops and nothing calls for heat
    hass.states.async_set("sensor.test_outside_sensor", str(FROST_GUARD_OUTSIDE_TEMPERATURE + FROST_GUARD_HYSTERESIS + 0.5))
    await heating.async_control_heating_loop()
    assert not heating.extra_state_attributes["frost_guard"]
    assert not heating.heat_demand

    # A TRV that is available again takes over from the frost guard
    hass.states.async_set("sensor.test_outside_sensor", "-15.0")
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["frost_guard"]

    set_trv(hass, 19.0, 20.0, "idle")
    await heating.async_control_heating_loop()
    assert not heating.extra_state_attributes["frost_guard"]
    assert not heating.heat_demand


@pytest.mark.parametrize("domains, data, options, config", setup(48, -15.0, **{CONF_ZONES: []}))
async def test_frost_guard_without_zones(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(48)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    # Without any zone, the only thing we do is protect against frost
    assert heating.extra_state_attributes["frost_guard"]
    assert heating.heat_demand

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

# Without a minimum difference, so the original scenarios of SAT with a room 0.1 below its target still heat
ZONE = [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, CONF_ZONE_MINIMUM_DIFFERENCE: 0}]


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


def set_trv(hass: HomeAssistant, current: float, target: float, hvac_action: str | None = None, opening: str = "100", **kwargs) -> None:
    attributes = {"current_temperature": current, "temperature": target, "hvac_modes": [HVACMode.HEAT]}
    if hvac_action is not None:
        attributes["hvac_action"] = hvac_action

    hass.states.async_set("sensor.radiator_1_pi_heating_demand", opening)

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
    assert heating.target_temperature == 20.0
    assert heating.heating_curve.value is not None
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


@pytest.mark.parametrize("domains, data, options, config", setup(**{CONF_OUTSIDE_FALLBACK: False}))
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
    assert heating.target_temperature == OPTIONS_DEFAULTS[CONF_FROST_GUARD_TEMPERATURE]
    assert heating.heating_curve.value == 32.6
    assert heating.requested_setpoint == 32.6

    # Warmer outside, but within the hysteresis, it keeps guarding
    hass.states.async_set("sensor.test_outside_sensor", str(OPTIONS_DEFAULTS[CONF_FROST_GUARD_OUTSIDE_TEMPERATURE] + 0.5))
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["frost_guard"]

    # Above it, the frost guard stops and nothing calls for heat
    hass.states.async_set("sensor.test_outside_sensor", str(OPTIONS_DEFAULTS[CONF_FROST_GUARD_OUTSIDE_TEMPERATURE] + FROST_GUARD_HYSTERESIS + 0.5))
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


@pytest.mark.parametrize("domains, data, options, config", setup(48, 0.0, **{CONF_HEATING_CURVE_COEFFICIENT: 2.0, CONF_FROST_GUARD_TEMPERATURE: 15.0, CONF_FROST_GUARD_OUTSIDE_TEMPERATURE: 1.0}))
async def test_frost_guard_is_configurable(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await coordinator.async_set_boiler_temperature(48)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    # Below the configured outside temperature, heating for the configured temperature
    assert heating.extra_state_attributes["frost_guard"]
    assert heating.target_temperature == 15.0

    hass.states.async_set("sensor.test_outside_sensor", "2.5")
    await heating.async_control_heating_loop()
    assert not heating.extra_state_attributes["frost_guard"]


@pytest.mark.parametrize("domains, data, options, config", setup(**{CONF_OUTSIDE_FALLBACK_HOURS: 6, CONF_OUTSIDE_FALLBACK_TEMPERATURE: 5.0}))
async def test_keeps_heating_without_outside_temperature(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 19.0, 20.0, "heating")

    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["outside_temperature_source"] == OUTSIDE_SOURCE_SENSOR

    # The outside temperature is gone, so we keep heating on the last known value
    hass.states.async_set("sensor.test_outside_sensor", STATE_UNAVAILABLE)
    await heating.async_control_heating_loop()

    assert heating.current_outside_temperature == 9.9
    assert heating.extra_state_attributes["outside_temperature_source"] == OUTSIDE_SOURCE_LAST_KNOWN
    assert not heating.extra_state_attributes["failsafe"]
    assert coordinator.flame_active

    # Too long ago, so we assume the configured outside temperature
    heating._last_outside_temperature_time -= 6 * 3600 + 1
    await heating.async_control_heating_loop()

    assert heating.current_outside_temperature == 5.0
    assert heating.extra_state_attributes["outside_temperature_source"] == OUTSIDE_SOURCE_ASSUMED
    assert coordinator.flame_active

    # It is back
    hass.states.async_set("sensor.test_outside_sensor", "8.0")
    await heating.async_control_heating_loop()

    assert heating.current_outside_temperature == 8.0
    assert heating.extra_state_attributes["outside_temperature_source"] == OUTSIDE_SOURCE_SENSOR


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_last_known_outside_temperature_survives_a_restart(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    from homeassistant.core import State

    attributes = heating.extra_state_attributes
    assert attributes["last_outside_temperature"] == 9.9
    assert attributes["last_outside_temperature_time"] is not None

    # What we stored is what we restore
    from unittest.mock import AsyncMock, patch

    from time import time

    # Home Assistant restarts while the outside sensor is unavailable: we continue from what we knew
    hass.states.async_set("sensor.test_outside_sensor", STATE_UNAVAILABLE)
    restored = State(heating.entity_id, STATE_ON, {"last_outside_temperature": 4.5, "last_outside_temperature_time": time() - 60})
    with patch.object(heating, "async_get_last_state", AsyncMock(return_value=restored)):
        await heating._restore_previous_state_or_set_defaults()

    assert heating.current_outside_temperature == 4.5
    assert heating.extra_state_attributes["outside_temperature_source"] == OUTSIDE_SOURCE_LAST_KNOWN


@pytest.mark.parametrize("domains, data, options, config", setup(40, 12.9, **{CONF_HEATING_CURVE_COEFFICIENT: 2.0, CONF_OVERSHOOT_HOLD: 1.2}))
async def test_a_nearly_closed_valve_asks_for_a_low_flow_temperature(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    # Today's test in the study: just below the target, the TRV still heating with its valve nearly closed
    await add_trv(hass, "Woonkamer", "radiator_1")
    await coordinator.async_set_boiler_temperature(40)
    await heating.async_turn_on()

    async def requested(opening: str) -> float:
        set_trv(hass, 22.1, 22.5, "heating", opening)
        heating._async_control_pid(True)
        await heating.async_control_heating_loop()
        return heating.requested_setpoint

    nearly_closed = await requested("11")
    wide_open = await requested("95")

    # Nearly closed, it follows the real difference of 0.4 °C instead of the full hold of 1.2
    assert heating.areas.errors[0].value == 1.14
    assert nearly_closed < 50
    assert wide_open > nearly_closed + 10


CIRCULATE = {CONF_CIRCULATE: True, CONF_CIRCULATE_MINIMUM_TEMPERATURE: 35, CONF_CIRCULATE_MAXIMUM_MINUTES: 15}


async def start_circulating(hass: HomeAssistant, heating: SatHeating, coordinator: SatFakeCoordinator, flame) -> None:
    """The study still wants a little heat, with its valve too far closed for the boiler, and the water is still warm."""
    await add_trv(hass, "Woonkamer", "radiator_1")
    set_trv(hass, 22.4, 22.5, "heating", "12")

    flame.return_value = False
    await coordinator.async_set_boiler_temperature(50)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()


@pytest.mark.parametrize("domains, data, options, config", setup(**CIRCULATE))
async def test_circulates_the_warm_water_that_is_left(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from unittest.mock import PropertyMock, patch

    with patch.object(type(coordinator), "flame_active", new_callable=PropertyMock) as flame:
        await start_circulating(hass, heating, coordinator, flame)

        # Not firing the boiler, but central heating on with the minimum setpoint: the boiler only pumps
        assert not heating.heat_demand
        assert heating.circulating
        assert coordinator.setpoint == MINIMUM_SETPOINT
        assert coordinator.device_active

        # The water is no longer warm enough to bother
        await coordinator.async_set_boiler_temperature(34)
        await heating.async_control_heating_loop()
        assert not heating.circulating
        assert not coordinator.device_active

        # Warm again without a burn, so nothing new to circulate
        await coordinator.async_set_boiler_temperature(50)
        await heating.async_control_heating_loop()
        assert not heating.circulating

        # After a burn it may circulate again
        flame.return_value = True
        await heating.async_control_heating_loop()
        flame.return_value = False
        await heating.async_control_heating_loop()
        assert heating.circulating


@pytest.mark.parametrize("domains, data, options, config", setup(**CIRCULATE))
async def test_circulates_for_a_limited_time(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from unittest.mock import PropertyMock, patch

    with patch.object(type(coordinator), "flame_active", new_callable=PropertyMock) as flame:
        await start_circulating(hass, heating, coordinator, flame)
        assert heating.circulating

        heating._circulating_since -= 15 * 60 + 1
        await heating.async_control_heating_loop()

        assert not heating.circulating
        assert not coordinator.device_active


@pytest.mark.parametrize("domains, data, options, config", setup(**CIRCULATE))
async def test_stops_circulating_for_good_when_the_boiler_fires(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from unittest.mock import PropertyMock, patch

    with patch.object(type(coordinator), "flame_active", new_callable=PropertyMock) as flame:
        await start_circulating(hass, heating, coordinator, flame)
        assert heating.circulating

        # The boiler fires on its own, although we only wanted it to pump
        flame.return_value = True
        await heating.async_control_heating_loop()
        assert not heating.circulating
        assert not coordinator.device_active

        # Even after a burn, we don't try again
        flame.return_value = False
        await coordinator.async_set_boiler_temperature(50)
        await heating.async_control_heating_loop()
        assert not heating.circulating


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_does_not_circulate_unless_turned_on(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from unittest.mock import PropertyMock, patch

    with patch.object(type(coordinator), "flame_active", new_callable=PropertyMock) as flame:
        await start_circulating(hass, heating, coordinator, flame)

        assert not heating.circulating
        assert not coordinator.device_active


@pytest.mark.parametrize("domains, data, options, config", setup(**{CONF_ZONES: [{**ZONE[0], CONF_ZONE_BOILER_MODE: True}]}))
async def test_boiler_mode_tells_the_trvs_whether_heat_is_available(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from homeassistant.helpers import entity_registry

    await add_trv(hass, "Woonkamer", "radiator_1")
    device_id = entity_registry.async_get(hass).async_get("climate.radiator_1").device_id
    entity_registry.async_get(hass).async_get_or_create("switch", "mqtt", "0xradiator_1_heat_available_zigbee2mqtt", suggested_object_id="radiator_1_heat_available", device_id=device_id)
    await hass.async_block_till_done()

    told = []

    async def tell(call) -> None:
        told.append((call.service, call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", tell)
    hass.services.async_register("switch", "turn_off", tell)
    hass.states.async_set("switch.radiator_1_heat_available", "on")

    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()

    # Nobody wants heat: the TRV hears no heat is coming, once
    set_trv(hass, 21.0, 20.0, "idle")
    await heating.async_control_heating_loop()
    await heating.async_control_heating_loop()
    assert told == [("turn_off", "switch.radiator_1_heat_available")]

    # The room calls for heat and the boiler fires: heat is available
    told.clear()
    set_trv(hass, 19.0, 20.0, "heating")
    await heating.async_control_heating_loop()
    assert told == [("turn_on", "switch.radiator_1_heat_available")]

    # Switched off, the TRV gets the factory default back
    hass.states.async_set("switch.radiator_1_heat_available", "off")
    told.clear()
    await heating.async_turn_off()
    assert told == [("turn_on", "switch.radiator_1_heat_available")]


@pytest.mark.parametrize("domains, data, options, config", setup(**{CONF_OUTSIDE_FALLBACK: False, CONF_ZONES: [{**ZONE[0], CONF_ZONE_BOILER_MODE: True}]}))
async def test_boiler_mode_in_failsafe_heat_is_not_available(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from homeassistant.helpers import entity_registry

    await add_trv(hass, "Woonkamer", "radiator_1")
    device_id = entity_registry.async_get(hass).async_get("climate.radiator_1").device_id
    entity_registry.async_get(hass).async_get_or_create("switch", "mqtt", "0xradiator_1_heat_available_zigbee2mqtt", suggested_object_id="radiator_1_heat_available", device_id=device_id)
    await hass.async_block_till_done()

    told = []

    async def tell(call) -> None:
        told.append((call.service, call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", tell)
    hass.services.async_register("switch", "turn_off", tell)
    hass.states.async_set("switch.radiator_1_heat_available", "on")

    # The room calls for heat and the boiler fires: heat is available
    set_trv(hass, 19.0, 20.0, "heating")
    await coordinator.async_set_boiler_temperature(57)
    await heating.async_turn_on()
    await heating.async_control_heating_loop()
    assert coordinator.flame_active
    assert told == []

    # Without the outside temperature the boiler is turned off, so the TRV hears no heat is coming
    hass.states.async_set("sensor.test_outside_sensor", STATE_UNAVAILABLE)
    await heating.async_control_heating_loop()
    assert heating.extra_state_attributes["failsafe"]
    assert told == [("turn_off", "switch.radiator_1_heat_available")]


@pytest.mark.parametrize("domains, data, options, config", setup())
async def test_without_boiler_mode_heat_stays_available(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from homeassistant.helpers import entity_registry

    await add_trv(hass, "Woonkamer", "radiator_1")
    device_id = entity_registry.async_get(hass).async_get("climate.radiator_1").device_id
    entity_registry.async_get(hass).async_get_or_create("switch", "mqtt", "0xradiator_1_heat_available_zigbee2mqtt", suggested_object_id="radiator_1_heat_available", device_id=device_id)
    await hass.async_block_till_done()

    told = []

    async def tell(call) -> None:
        told.append((call.service, call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", tell)
    hass.services.async_register("switch", "turn_off", tell)

    # Left in boiler mode from before: it hears heat is available again, and nothing else
    hass.states.async_set("switch.radiator_1_heat_available", "off")
    set_trv(hass, 21.0, 20.0, "idle")
    await heating.async_turn_on()
    await heating.async_control_heating_loop()
    await heating.async_control_heating_loop()

    assert told == [("turn_on", "switch.radiator_1_heat_available")]


@pytest.mark.parametrize("domains, data, options, config", setup(**{CONF_ZONES: [{**ZONE[0], CONF_ZONE_BOILER_MODE: True}]}))
async def test_boiler_mode_is_left_alone_on_a_reload_and_undone_on_removal(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    from homeassistant.helpers import entity_registry

    await add_trv(hass, "Woonkamer", "radiator_1")
    device_id = entity_registry.async_get(hass).async_get("climate.radiator_1").device_id
    entity_registry.async_get(hass).async_get_or_create("switch", "mqtt", "0xradiator_1_heat_available_zigbee2mqtt", suggested_object_id="radiator_1_heat_available", device_id=device_id)
    await hass.async_block_till_done()

    set_trv(hass, 21.0, 20.0, "idle")
    await heating.async_turn_on()
    await heating.async_control_heating_loop()

    told = []

    async def tell(call) -> None:
        told.append((call.service, call.data["entity_id"]))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", tell)
    hass.services.async_register("switch", "turn_off", tell)

    # The TRV already heard no heat is available
    hass.states.async_set("switch.radiator_1_heat_available", "off")

    # Changing a setting reloads the integration, which shouldn't make the TRV switch back and forth
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await hass.data[DOMAIN][entry.entry_id][CLIMATE].async_control_heating_loop()
    assert told == []

    # Removing the integration gives it the factory default back
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert told == [("turn_on", "switch.radiator_1_heat_available")]

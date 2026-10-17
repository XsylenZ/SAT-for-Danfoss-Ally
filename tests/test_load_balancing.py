"""The tests for load balancing: the TRVs of a zone share the mean of their load estimates."""
from datetime import timedelta

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util import dt
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.switch import SatHeating
from tests.helpers import add_trv

TRVS = ("tv_hoek_r", "tv_hoek_l", "eetkamer_r")


def setup(load_balancing: bool):
    return [(
        [(TEMPLATE_DOMAIN, 1)],
        {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
        {CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_TOGETHER, CONF_ZONE_LOAD_BALANCING: load_balancing}]},
        {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )]


async def add_living_room(hass: HomeAssistant, estimates: dict[str, float | str], room_mean: float = LOAD_UNDEFINED) -> list[ServiceCall]:
    """Add the TRVs of the living room with their load estimates, and record what is written to them."""
    writes = []

    async def set_value(call: ServiceCall) -> None:
        writes.append(call)
        hass.states.async_set(call.data["entity_id"], str(call.data["value"]))

    hass.services.async_register("number", "set_value", set_value)

    for name in TRVS:
        await add_trv(hass, "Woonkamer", name)
        hass.states.async_set(f"climate.{name}", "heat", {"current_temperature": 20.0, "temperature": 20.0, "hvac_action": "idle"})
        hass.states.async_set(f"sensor.{name}_load_estimate", str(estimates[name]))
        hass.states.async_set(f"number.{name}_load_room_mean", str(room_mean))

    await hass.async_block_till_done()
    return writes


def written(writes: list[ServiceCall]) -> dict[str, float]:
    return {call.data["entity_id"]: call.data["value"] for call in writes}


@pytest.mark.parametrize("domains, data, options, config", setup(True))
async def test_writes_the_mean_of_the_load_estimates(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    # One TRV doesn't know its load yet, it is left out of the mean but still gets it
    writes = await add_living_room(hass, {"tv_hoek_r": 10, "tv_hoek_l": 25, "eetkamer_r": LOAD_UNDEFINED})
    await heating.async_turn_on()

    await heating.async_balance_loads()

    assert written(writes) == {
        "number.tv_hoek_r_load_room_mean": 18,
        "number.tv_hoek_l_load_room_mean": 18,
        "number.eetkamer_r_load_room_mean": 18,
    }
    assert heating.extra_state_attributes["zones"]["woonkamer"]["load_room_mean"] == 18

    # Nothing changed, so nothing is written again
    writes.clear()
    await heating.async_balance_loads()
    assert writes == []


@pytest.mark.parametrize("domains, data, options, config", setup(True))
async def test_leaves_out_stale_and_unavailable_trvs(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    writes = await add_living_room(hass, {"tv_hoek_r": 10, "tv_hoek_l": 20, "eetkamer_r": 90})
    await heating.async_turn_on()

    # An unavailable TRV is left out completely, a stale estimate is left out of the mean
    hass.states.async_set("climate.eetkamer_r", STATE_UNAVAILABLE)
    stale = dt.utcnow() - timedelta(seconds=AREA_STATE_MAX_AGE + 60)
    hass.states.async_set("sensor.tv_hoek_l_load_estimate", "20", timestamp=stale.timestamp())

    await heating.async_balance_loads()

    # Only one estimate is left, which can't be shared: the TRVs regulate on their own
    assert written(writes) == {}
    assert heating.extra_state_attributes["zones"]["woonkamer"]["load_room_mean"] is None


@pytest.mark.parametrize("domains, data, options, config", setup(False))
async def test_without_load_balancing_the_mean_is_undefined_again(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    # The TRVs still have a mean from when load balancing was on
    writes = await add_living_room(hass, {"tv_hoek_r": 10, "tv_hoek_l": 25, "eetkamer_r": 40}, room_mean=18)
    await heating.async_turn_on()

    await heating.async_balance_loads()

    assert written(writes) == {f"number.{name}_load_room_mean": LOAD_UNDEFINED for name in TRVS}


@pytest.mark.parametrize("domains, data, options, config", setup(True))
async def test_switched_off_leaves_the_trvs_alone(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    writes = await add_living_room(hass, {"tv_hoek_r": 10, "tv_hoek_l": 25, "eetkamer_r": 40})

    await heating.async_balance_loads()

    assert writes == []

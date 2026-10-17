"""The tests for reloading the integration, which must not lose the connection to the gateway."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.serial import SatSerialCoordinator
from tests.const import DEFAULT_USER_DATA


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
            {},
            {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )],
])
async def test_changing_options_reloads_once(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][COORDINATOR]

    with patch.object(type(coordinator), "async_will_remove_from_hass", autospec=True) as disconnect:
        for overshoot_hold in (0.6, 0.7, 0.8):
            hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_OVERSHOOT_HOLD: overshoot_hold})
            await hass.async_block_till_done()

    # Every change reloads exactly once, with a single connection at a time
    assert disconnect.call_count == 3
    assert len(entry.update_listeners) == 1
    assert entry.state is ConfigEntryState.LOADED

    # And we keep controlling with the new settings
    heating = hass.data[DOMAIN][entry.entry_id][CLIMATE]
    assert heating.areas._config_options[CONF_OVERSHOOT_HOLD] == 0.8
    assert hass.states.get(heating.entity_id) is not None


async def test_gateway_that_does_not_accept_us_is_retried_later(hass: HomeAssistant) -> None:
    coordinator = SatSerialCoordinator(hass, "socket://192.0.2.1:25238", {**DEFAULT_USER_DATA, CONF_MODE: MODE_SERIAL})

    async def never_connects(**_kwargs):
        await asyncio.Event().wait()

    coordinator._api.connect = AsyncMock(side_effect=never_connects)
    coordinator._api.disconnect = AsyncMock()

    with patch("custom_components.ally_otgw.serial.CONNECT_TIMEOUT", 0.05):
        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_setup()

    # The attempt is stopped, instead of trying forever in the background
    coordinator._api.disconnect.assert_awaited_once()

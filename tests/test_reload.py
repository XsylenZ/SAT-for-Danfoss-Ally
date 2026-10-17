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


async def test_gateway_entities_are_kept_and_added_when_their_value_arrives(hass: HomeAssistant) -> None:
    from types import SimpleNamespace
    from homeassistant.helpers import entity_registry
    from pyotgw.vars import BOILER, THERMOSTAT, OTGW, DATA_MASTER_CH_ENABLED, DATA_SLAVE_FLAME_ON, DATA_SLAVE_CH_ACTIVE, OTGW_THRM_DETECT
    from custom_components.ally_otgw.serial import async_add_gateway_entities

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_NAME: "OTGW"})
    entry.add_to_hass(hass)

    # We had this entity before the reload, but the gateway hasn't reported its value again yet
    entity_registry.async_get(hass).async_get_or_create("binary_sensor", DOMAIN, f"OTGW-{BOILER}-{DATA_MASTER_CH_ENABLED}", config_entry=entry)

    listeners = []
    data = {BOILER: {DATA_SLAVE_FLAME_ON: False}, THERMOSTAT: {}, OTGW: {OTGW_THRM_DETECT: "D"}}
    coordinator = SimpleNamespace(hass=hass, data=data, async_add_listener=lambda listener: listeners.append(listener) or (lambda: None))
    infos = {key: SimpleNamespace(status_sources=[BOILER, THERMOSTAT]) for key in (DATA_MASTER_CH_ENABLED, DATA_SLAVE_FLAME_ON, DATA_SLAVE_CH_ACTIVE)}

    added = []
    async_add_gateway_entities(
        coordinator, entry, "binary_sensor", infos,
        lambda source, key: f"OTGW-{source}-{key}",
        lambda info, key, source: (source, key),
        added.extend,
    )

    # The known entity comes back, the one with a value is added, nothing for the thermostat without a thermostat
    assert sorted(added) == sorted([(BOILER, DATA_MASTER_CH_ENABLED), (BOILER, DATA_SLAVE_FLAME_ON)])

    # A value that arrives later gets its entity then, and only once
    data[BOILER][DATA_SLAVE_CH_ACTIVE] = True
    listeners[0]()
    listeners[0]()
    assert added.count((BOILER, DATA_SLAVE_CH_ACTIVE)) == 1

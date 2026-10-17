"""The tests for reconfiguring the integration and its diagnostics."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.diagnostics import async_get_config_entry_diagnostics
from custom_components.ally_otgw.switch import SatHeating
from tests.helpers import add_trv

SERIAL = {CONF_NAME: "OTGW", CONF_MODE: MODE_SERIAL, CONF_DEVICE: "socket://192.168.1.50:25238", CONF_OUTSIDE_SENSOR_ENTITY_ID: ["weather.home"]}


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id})


def gateway(connects: bool) -> MagicMock:
    mock = MagicMock()
    mock.connection.connect = AsyncMock(return_value=connects)
    mock.disconnect = AsyncMock()
    return mock


async def test_change_the_gateway_address(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=SERIAL, unique_id=SERIAL[CONF_DEVICE], version=10)
    entry.add_to_hass(hass)

    result = await start(hass, entry)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["reconfigure_connection", "reconfigure_sensors", "calibrate_system"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "reconfigure_connection"})
    assert result["step_id"] == "reconfigure_connection"
    assert result["description_placeholders"] == {"device": "socket://192.168.1.50:25238"}

    # An invalid address, and an address where nothing answers, are refused
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE: "192.168.1.60"})
    assert result["errors"] == {"base": "invalid_device"}

    with patch("custom_components.ally_otgw.config_flow.OpenThermGateway", return_value=gateway(False)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE: "socket://192.168.1.60:25238"})
    assert result["errors"] == {"base": "connection"}

    # A gateway that answers is saved, keeping everything else
    with patch("custom_components.ally_otgw.config_flow.OpenThermGateway", return_value=gateway(True)), \
            patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE: " socket://192.168.1.60:25238 "})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**SERIAL, CONF_DEVICE: "socket://192.168.1.60:25238"}
    assert entry.unique_id == "socket://192.168.1.60:25238"
    reload.assert_called_once_with(entry.entry_id)


async def test_keeping_the_address_does_not_test_our_own_connection(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=SERIAL, unique_id=SERIAL[CONF_DEVICE], version=10)
    entry.add_to_hass(hass)

    result = await start(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "reconfigure_connection"})

    # The gateway accepts only one connection, which is ours, so testing it would fail
    with patch("custom_components.ally_otgw.config_flow.OpenThermGateway") as otgw, patch.object(hass.config_entries, "async_schedule_reload"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_DEVICE: SERIAL[CONF_DEVICE]})

    otgw.assert_not_called()
    assert result["reason"] == "reconfigure_successful"


async def test_change_the_outside_temperature(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=SERIAL, unique_id=SERIAL[CONF_DEVICE], version=10)
    entry.add_to_hass(hass)

    result = await start(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "reconfigure_sensors"})
    assert result["step_id"] == "reconfigure_sensors"

    with patch.object(hass.config_entries, "async_schedule_reload"):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_OUTSIDE_SENSOR_ENTITY_ID: ["sensor.outside", "weather.home"]})

    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {**SERIAL, CONF_OUTSIDE_SENSOR_ENTITY_ID: ["sensor.outside", "weather.home"]}


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
            {CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}]},
            {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )],
])
async def test_diagnostics(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    await add_trv(hass, "Woonkamer", "radiator_1")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["settings"][CONF_OUTSIDE_SENSOR_ENTITY_ID] == "sensor.test_outside_sensor"
    assert diagnostics["options"][CONF_ZONES] == [{CONF_ZONE_AREA: "woonkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT}]
    assert diagnostics["heating"]["on"] is False
    assert diagnostics["heating"]["outside_temperature"] == 9.9
    assert diagnostics["zones"]["woonkamer"]["trvs"] == ["climate.radiator_1"]
    assert [trv["entity_id"] for trv in diagnostics["trvs"]] == ["climate.radiator_1"]
    assert diagnostics["boiler"]["device_type"] is not None


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
            {},
            {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )],
])
async def test_calibrating_pauses_the_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from homeassistant.config_entries import ConfigEntryState

    result = await start(hass, entry)
    assert result["menu_options"] == ["reconfigure_sensors", "calibrate_system"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "calibrate_system"})

    with patch("custom_components.ally_otgw.config_flow.OvershootProtection") as overshoot_protection:
        overshoot_protection.return_value.calculate = AsyncMock(return_value=45.0)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "calibrate"})

        # The gateway accepts only one connection, so the integration lets go of it while we calibrate
        assert entry.state is ConfigEntryState.NOT_LOADED

        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["step_id"] == "calibrated"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "finish"})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_MINIMUM_SETPOINT] == 45.0
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
            {},
            {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )],
])
async def test_failed_calibration_resumes_the_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    from homeassistant.config_entries import ConfigEntryState

    result = await start(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "calibrate_system"})

    with patch("custom_components.ally_otgw.config_flow.OvershootProtection") as overshoot_protection:
        overshoot_protection.return_value.calculate = AsyncMock(return_value=None)
        await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "calibrate"})
        await hass.async_block_till_done()

    # The flow ended without a value, and we control the boiler again
    await hass.async_block_till_done()
    assert hass.config_entries.flow.async_progress() == []
    assert entry.state is ConfigEntryState.LOADED

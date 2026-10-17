from __future__ import annotations

import asyncio
import logging
from typing import Optional, Any, Mapping, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity import Entity
from homeassistant.exceptions import ConfigEntryNotReady
from pyotgw import vars as gw_vars, OpenThermGateway
from pyotgw.vars import *
from serial import SerialException

from ..const import DOMAIN
from ..coordinator import DeviceState, SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)

# How long we wait for the gateway to accept our connection, before Home Assistant retries later
CONNECT_TIMEOUT = 30

# Sensors
TRANSLATE_SOURCE = {
    gw_vars.OTGW: None,
    gw_vars.BOILER: "Boiler",
    gw_vars.THERMOSTAT: "Thermostat",
}


@callback
def async_add_gateway_entities(
        coordinator: SatSerialCoordinator,
        config_entry: ConfigEntry,
        platform: str,
        infos: dict[str, Any],
        unique_id: Callable[[str, str], str],
        create: Callable[[Any, str, str], Entity],
        async_add_entities: Callable[[list[Entity]], None],
) -> None:
    """Add an entity for every value the gateway reports.

    Right after (re)connecting, the gateway hasn't reported every value yet. So we always add the entities we had before,
    and add the others as soon as the gateway reports their value, instead of only looking once at setup.
    """
    registry = entity_registry.async_get(coordinator.hass)
    has_thermostat = coordinator.data[OTGW].get(OTGW_THRM_DETECT) != "D"
    added: set[tuple[str, str]] = set()

    @callback
    def add_entities() -> None:
        entities = []

        for key, info in infos.items():
            for source in info.status_sources:
                if source == THERMOSTAT and has_thermostat is False:
                    continue

                if (source, key) in added:
                    continue

                if coordinator.data[source].get(key) is None and registry.async_get_entity_id(platform, DOMAIN, unique_id(source, key)) is None:
                    continue

                added.add((source, key))
                entities.append(create(info, key, source))

        if entities:
            async_add_entities(entities)

    add_entities()
    config_entry.async_on_unload(coordinator.async_add_listener(add_entities))


class SatSerialCoordinator(SatDataUpdateCoordinator):
    """Class to manage to fetch data from the OTGW Gateway using pyotgw."""

    def __init__(self, hass: HomeAssistant, port: str, config_data: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> None:
        """Initialize."""
        super().__init__(hass, config_data, options)
        self.async_set_updated_data(DEFAULT_STATUS)

        self._port: str = port
        self._hass_loop = hass.loop
        self._api: OpenThermGateway = OpenThermGateway()

        async def _publish(data: dict) -> None:
            self._hass_loop.call_soon_threadsafe(self.async_set_updated_data, data)

        self._publish_callback = _publish
        self._api.subscribe(self._publish_callback)

    @property
    def device_id(self) -> str:
        return self._port

    @property
    def device_type(self) -> str:
        return "OpenThermGateway (via serial)"

    @property
    def device_active(self) -> bool:
        return bool(self.get(DATA_MASTER_CH_ENABLED) or False)

    @property
    def hot_water_active(self) -> bool:
        return bool(self.get(DATA_SLAVE_DHW_ACTIVE) or False)

    @property
    def supports_setpoint_management(self) -> bool:
        return True

    @property
    def supports_hot_water_setpoint_management(self):
        return True

    @property
    def supports_maximum_setpoint_management(self) -> bool:
        return True

    @property
    def supports_relative_modulation(self) -> bool:
        return True

    @property
    def setpoint(self) -> Optional[float]:
        if (setpoint := self.get(DATA_CONTROL_SETPOINT)) is not None:
            return float(setpoint)

        return None

    @property
    def hot_water_setpoint(self) -> Optional[float]:
        if (setpoint := self.get(DATA_DHW_SETPOINT)) is not None:
            return float(setpoint)

        return super().hot_water_setpoint

    @property
    def boiler_temperature(self) -> Optional[float]:
        if (value := self.get(DATA_CH_WATER_TEMP)) is not None:
            return float(value)

        return super().boiler_temperature

    @property
    def return_temperature(self) -> Optional[float]:
        if (value := self.get(DATA_RETURN_WATER_TEMP)) is not None:
            return float(value)

        return super().return_temperature

    @property
    def minimum_hot_water_setpoint(self) -> float:
        if (setpoint := self.get(DATA_SLAVE_DHW_MIN_SETP)) is not None:
            return float(setpoint)

        return super().minimum_hot_water_setpoint

    @property
    def maximum_hot_water_setpoint(self) -> float:
        if (setpoint := self.get(DATA_SLAVE_DHW_MAX_SETP)) is not None:
            return float(setpoint)

        return super().maximum_hot_water_setpoint

    @property
    def relative_modulation_value(self) -> Optional[float]:
        if (value := self.get(DATA_REL_MOD_LEVEL)) is not None:
            return float(value)

        return super().relative_modulation_value

    @property
    def boiler_capacity(self) -> Optional[float]:
        if (value := self.get(DATA_SLAVE_MAX_CAPACITY)) is not None:
            return float(value)

        return super().boiler_capacity

    @property
    def minimum_relative_modulation_value(self) -> Optional[float]:
        if (value := self.get(DATA_SLAVE_MIN_MOD_LEVEL)) is not None:
            return float(value)

        return super().minimum_relative_modulation_value

    @property
    def maximum_relative_modulation_value(self) -> Optional[float]:
        if (value := self.get(DATA_SLAVE_MAX_RELATIVE_MOD)) is not None:
            return float(value)

        return super().maximum_relative_modulation_value

    @property
    def member_id(self) -> Optional[int]:
        if (value := self.get(DATA_SLAVE_MEMBERID)) is not None:
            return int(value)

        return None

    @property
    def flame_active(self) -> bool:
        return bool(self.get(DATA_SLAVE_FLAME_ON))

    def get(self, key: str) -> Optional[Any]:
        """Get the value for the given `key` from the boiler data."""
        return self.data[BOILER].get(key)

    async def async_connect(self) -> SatSerialCoordinator:
        # pyotgw keeps retrying forever when the gateway doesn't accept us, e.g. while it still holds on to our previous
        # connection. Give up after a while instead, so Home Assistant retries the setup later rather than hanging.
        connect = asyncio.ensure_future(self._api.connect(port=self._port, timeout=5))
        done, _ = await asyncio.wait({connect}, timeout=CONNECT_TIMEOUT)

        if not done:
            await self._api.disconnect()
            raise ConfigEntryNotReady(f"Could not connect to gateway at {self._port} within {CONNECT_TIMEOUT} seconds")

        try:
            if not connect.result():
                raise ConfigEntryNotReady(f"Could not connect to gateway at {self._port}")
        except (asyncio.TimeoutError, ConnectionError, SerialException) as exception:
            raise ConfigEntryNotReady(f"Could not connect to gateway at {self._port}: {exception}") from exception

        return self

    async def async_setup(self) -> None:
        await self.async_connect()

    async def async_will_remove_from_hass(self) -> None:
        if self._publish_callback is not None:
            try:
                self._api.unsubscribe(self._publish_callback)
            except Exception:  # pragma: no cover - best effort cleanup
                _LOGGER.debug("Failed to unsubscribe serial listener", exc_info=True)
            finally:
                self._publish_callback = None

        await self._graceful_disconnect()

    async def async_set_control_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_control_setpoint(value)

        await super().async_set_control_setpoint(value)

    async def async_set_control_hot_water_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_dhw_setpoint(value)

        await super().async_set_control_hot_water_setpoint(value)

    async def async_set_control_thermostat_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_target_temp(value)

        await super().async_set_control_thermostat_setpoint(value)

    async def async_set_heater_state(self, state: DeviceState) -> None:
        if not self._simulation:
            await self._api.set_ch_enable_bit(1 if state == DeviceState.ON else 0)

        await super().async_set_heater_state(state)

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if not self._simulation:
            await self._api.set_max_relative_mod(value)

        await super().async_set_control_max_relative_modulation(value)

    async def async_set_control_max_setpoint(self, value: float) -> None:
        if not self._simulation:
            await self._api.set_max_ch_setpoint(value)

        await super().async_set_control_max_setpoint(value)

    async def _graceful_disconnect(self) -> None:
        try:
            await self._api.set_control_setpoint(0)
            await self._api.set_max_relative_mod("-")
        except Exception:  # pragma: no cover - best effort cleanup
            _LOGGER.debug("Failed to reset serial gateway state before disconnect", exc_info=True)

        try:
            await self._api.disconnect()
        except Exception:  # pragma: no cover - best effort cleanup
            _LOGGER.debug("Error while disconnecting serial gateway", exc_info=True)

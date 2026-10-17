"""Adds config flow for Danfoss Ally OTGW."""
import asyncio
import logging
from typing import Optional, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import sensor, switch, valve, weather, climate, input_boolean
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON, STATE_ON
from homeassistant.core import callback
from homeassistant.helpers import area_registry, selector, entity_registry
from homeassistant.helpers.selector import SelectSelectorMode
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
from pyotgw import OpenThermGateway
from voluptuous import Marker

from . import SatDataUpdateCoordinatorFactory
from .const import *
from .coordinator import SatDataUpdateCoordinator
from .helpers import calculate_default_maximum_setpoint, snake_case
from .manufacturer import ManufacturerFactory, MANUFACTURERS
from .overshoot_protection import OvershootProtection
from .validators import valid_serial_device
from .zones import discover_trvs

DEFAULT_NAME = "Living Room"

ZONE_CHOICE = "zone"
ZONE_CHOICE_AREAS = "__areas__"
ZONE_CHOICE_SAVE = "__save__"

# The labels of the zones overview, which are built from the zones themselves and can't use the translation files
ZONE_TEXTS = {
    "en": {
        "independent": "Independent", "together": "Together",
        "areas": "Add or remove zones", "save": "Save and close",
        "rules": " (at least {minimum_open} TRVs or {minimum_demand}% open, at most {maximum_wait} min waiting)",
        "none": "No zones yet, choose *Add or remove zones*.", "no_trvs": "no Danfoss Ally TRVs found in this area",
    },
    "nl": {
        "independent": "Zelfstandig", "together": "Samen",
        "areas": "Zones toevoegen of verwijderen", "save": "Opslaan en sluiten",
        "rules": " (minstens {minimum_open} kranen of {minimum_demand}% open, hooguit {maximum_wait} min wachten)",
        "none": "Nog geen zones, kies *Zones toevoegen of verwijderen*.", "no_trvs": "geen Danfoss Ally-kranen gevonden in deze ruimte",
    },
}

_LOGGER = logging.getLogger(__name__)


class SatFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for SAT."""
    VERSION = 10
    MINOR_VERSION = 0

    calibration = None
    previous_heating_state = None
    overshoot_protection_value = None

    def __init__(self):
        """Initialize."""
        self.data = {}
        self.errors = {}
        self.config_entry = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry):
        return SatOptionsFlowHandler(config_entry)

    @callback
    def async_remove(self) -> None:
        if self.calibration is not None:
            self.calibration.cancel()

    async def async_step_user(self, _user_input: dict[str, Any] | None = None):
        """Handle user flow."""
        menu_options = [
            "mosquitto",
            "esphome",
            "serial",
            "switch"
        ]

        if self.show_advanced_options:
            menu_options.append("simulator")

        return self.async_show_menu(step_id="user", menu_options=menu_options)

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo):
        """Handle dhcp discovery."""
        _LOGGER.debug("Discovered OTGW at [socket://%s]", discovery_info.hostname)
        self.data[CONF_DEVICE] = f"socket://{discovery_info.hostname}:25238"

        # abort if we already have exactly this gateway id/host
        # reload the integration if the host got updated
        await self.async_set_unique_id(discovery_info.hostname)
        self._abort_if_unique_id_configured(updates=self.data)

        return await self.async_step_serial()

    async def async_step_mqtt(self, discovery_info: MqttServiceInfo):
        """Handle mqtt discovery."""
        _LOGGER.debug("Discovered MQTT at [mqtt://%s]", discovery_info.topic)

        # Mapping topic prefixes to handler methods and device IDs
        topic_mapping = {
            "ems-esp/": (MODE_MQTT_EMS, "ems-esp", self.async_step_mosquitto_ems),
            "OTGW/": (MODE_MQTT_OPENTHERM, discovery_info.topic[11:], self.async_step_mosquitto_opentherm),
        }

        # Check for matching prefix and handle appropriately
        for prefix, (mode, device_id, step_method) in topic_mapping.items():
            if discovery_info.topic.startswith(prefix):
                _LOGGER.debug("Identified gateway type %s: %s", mode[5:], device_id)
                self.data[CONF_MODE] = mode
                self.data[CONF_DEVICE] = device_id

                # Abort if the gateway is already registered, reload if necessary
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured(updates=self.data)

                return await step_method()

        _LOGGER.error("Unsupported MQTT topic format: %s", discovery_info.topic)
        return self.async_abort(reason="unsupported_gateway")

    async def async_step_mosquitto(self, _user_input: dict[str, Any] | None = None):
        """Entry step to select the MQTT mode and branch to a specific setup."""

        if _user_input is not None:
            self.errors = {}
            self.data.update(_user_input)

            if self.data[CONF_MODE] == MODE_MQTT_OPENTHERM:
                return await self.async_step_mosquitto_opentherm()

            if self.data[CONF_MODE] == MODE_MQTT_EMS:
                return await self.async_step_mosquitto_ems()

        return self.async_show_form(
            step_id="mosquitto",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_MODE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        mode=SelectSelectorMode.DROPDOWN,
                        options=[
                            selector.SelectOptionDict(value=MODE_MQTT_OPENTHERM, label="OpenTherm Gateway (For advanced boiler control)"),
                            selector.SelectOptionDict(value=MODE_MQTT_EMS, label="EMS-ESP (For Bosch, Junkers, Buderus systems)"),
                        ]
                    )
                ),
            }),
        )

    async def async_step_mosquitto_opentherm(self, _user_input: dict[str, Any] | None = None):
        """Setup specific to OpenTherm Gateway."""
        if _user_input is not None:
            self.data.update(_user_input)

            return await self.async_step_sensors()

        return self._create_mqtt_form("mosquitto_opentherm", "OTGW", "otgw-XXXXXXXXXXXX")

    async def async_step_mosquitto_ems(self, _user_input: dict[str, Any] | None = None):
        """Setup specific to EMS-ESP."""
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_DEVICE] = "ems-esp"

            return await self.async_step_sensors()

        return self._create_mqtt_form("mosquitto_ems", "ems-esp")

    async def async_step_esphome(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_ESPHOME

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="esphome",
            last_step=False,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default=self.data.get(CONF_DEVICE)): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(integration="esphome")
                ),
            }),
        )

    async def async_step_serial(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.errors = {}
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SERIAL

            if not valid_serial_device(self.data[CONF_DEVICE]):
                self.errors["base"] = "invalid_device"
                return await self.async_step_serial()

            gateway = OpenThermGateway()

            try:
                connected = await asyncio.wait_for(gateway.connection.connect(port=self.data[CONF_DEVICE]), timeout=5)
            except asyncio.TimeoutError:
                connected = False

            if not connected:
                self.errors["base"] = "connection"
                return await self.async_step_serial()

            await gateway.disconnect()
            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="serial",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE, default=self.data.get(CONF_DEVICE, "socket://otgw.local:25238")): str,
            }),
        )

    async def async_step_switch(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SWITCH

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="switch",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_DEVICE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[switch.DOMAIN, valve.DOMAIN, input_boolean.DOMAIN])
                ),
                vol.Required(CONF_MINIMUM_SETPOINT, default=50): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=100, step=1)
                )
            }),
        )

    async def async_step_simulator(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            self.data[CONF_MODE] = MODE_SIMULATOR
            self.data[CONF_DEVICE] = f"%s_%s".format(MODE_SIMULATOR, snake_case(_user_input.get(CONF_NAME)))

            return await self.async_step_sensors()

        return self.async_show_form(
            step_id="simulator",
            last_step=False,
            errors=self.errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_SIMULATED_HEATING, default=OPTIONS_DEFAULTS[CONF_SIMULATED_HEATING]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(CONF_SIMULATED_COOLING, default=OPTIONS_DEFAULTS[CONF_SIMULATED_COOLING]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1)
                ),
                vol.Required(CONF_MINIMUM_SETPOINT, default=OPTIONS_DEFAULTS[CONF_MINIMUM_SETPOINT]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=100, step=1)
                ),
                vol.Required(CONF_SIMULATED_WARMING_UP, default=OPTIONS_DEFAULTS[CONF_SIMULATED_WARMING_UP]): selector.TimeSelector()
            }),
        )

    async def async_step_reconfigure(self, _user_input: dict[str, Any] | None = None):
        self.config_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self.data = self.config_entry.data.copy()

        return await self.async_step_sensors()

    async def async_step_sensors(self, _user_input: dict[str, Any] | None = None):
        if self.config_entry is None:
            await self.async_set_unique_id(self.data[CONF_DEVICE], raise_on_progress=False)
            self._abort_if_unique_id_configured()

        if _user_input is not None:
            self.data.update(_user_input)

            # Danfoss Ally TRVs sit on radiators, the TRVs themselves are the room thermostats
            self.data[CONF_HEATING_SYSTEM] = HEATING_SYSTEM_RADIATORS

            if (await self.async_create_coordinator()).supports_setpoint_management:
                return await self.async_step_calibrate_system()

            return await self.async_step_automatic_gains()

        return self.async_show_form(
            last_step=False,
            step_id="sensors",
            data_schema=self.add_suggested_values_to_schema(vol.Schema({
                vol.Required(CONF_OUTSIDE_SENSOR_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=[sensor.DOMAIN, weather.DOMAIN], multiple=True)
                ),
            }), self.data),
        )

    async def async_step_automatic_gains(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)

            if not self.data[CONF_AUTOMATIC_GAINS]:
                return await self.async_step_pid_controller()

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="automatic_gains",
            data_schema=vol.Schema({vol.Required(CONF_AUTOMATIC_GAINS, default=True): bool})
        )

    async def async_step_calibrate_system(self, _user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="calibrate_system",
            menu_options=["calibrate", "overshoot_protection", "pid_controller"]
        )

    async def async_step_calibrate(self, _user_input: dict[str, Any] | None = None):
        # Let's see if we have already been configured before
        device_name = self.data[CONF_NAME]
        entities = entity_registry.async_get(self.hass)
        heating_id = entities.async_get_entity_id(switch.DOMAIN, DOMAIN, device_name.lower())

        async def start_calibration():
            try:
                coordinator = await self.async_create_coordinator()
                await coordinator.async_setup()
                await coordinator.async_added_to_hass()

                overshoot_protection = OvershootProtection(coordinator, self.data.get(CONF_HEATING_SYSTEM))
                self.overshoot_protection_value = await overshoot_protection.calculate()

                await coordinator.async_will_remove_from_hass()
            except asyncio.TimeoutError:
                _LOGGER.warning("Timed out during overshoot protection calculation.")
            except asyncio.CancelledError:
                _LOGGER.warning("Cancelled overshoot protection calculation.")

        if not self.calibration:
            self.calibration = self.hass.async_create_task(
                start_calibration()
            )

            # Make sure we don't control the boiler ourselves while calibrating
            if heating_id is not None and (state := self.hass.states.get(heating_id)) is not None:
                self.previous_heating_state = state.state
                await self.hass.services.async_call(switch.DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: heating_id}, blocking=True)

            # Make sure all climate valves are open
            for entity_id in [trv.climate for trv in discover_trvs(self.hass)]:
                data = {ATTR_ENTITY_ID: entity_id, climate.ATTR_HVAC_MODE: climate.HVACMode.HEAT}
                await self.hass.services.async_call(climate.DOMAIN, climate.SERVICE_SET_HVAC_MODE, data, blocking=True)

            return self.async_show_progress(
                step_id="calibrate",
                progress_task=self.calibration,
                progress_action="calibration",
            )

        if self.overshoot_protection_value is None:
            return self.async_abort(reason="unable_to_calibrate")

        self._enable_overshoot_protection(
            self.overshoot_protection_value
        )

        self.calibration = None
        self.overshoot_protection_value = None

        # Make sure to restore the heating after we are done
        if heating_id is not None and self.previous_heating_state == STATE_ON:
            await self.hass.services.async_call(switch.DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: heating_id}, blocking=True)

        return self.async_show_progress_done(next_step_id="calibrated")

    async def async_step_calibrated(self, _user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id="calibrated",
            description_placeholders=self.data,
            menu_options=["calibrate", "finish"],
        )

    async def async_step_overshoot_protection(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self._enable_overshoot_protection(
                _user_input[CONF_MINIMUM_SETPOINT]
            )

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="overshoot_protection",
            data_schema=vol.Schema({
                vol.Required(CONF_MINIMUM_SETPOINT, default=self.data.get(CONF_MINIMUM_SETPOINT, OPTIONS_DEFAULTS[CONF_MINIMUM_SETPOINT])): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=MINIMUM_SETPOINT, max=MAXIMUM_SETPOINT, step=1, unit_of_measurement="°C")
                ),
            })
        )

    async def async_step_pid_controller(self, _user_input: dict[str, Any] | None = None):
        self.data[CONF_AUTOMATIC_GAINS] = False

        if _user_input is not None:
            self.data.update(_user_input)

            if self.data[CONF_MODE] == MODE_SIMULATOR:
                return await self.async_step_finish()

            return await self.async_step_manufacturer()

        return self.async_show_form(
            last_step=False,
            step_id="pid_controller",
            data_schema=vol.Schema({
                vol.Required(CONF_PROPORTIONAL, default=self.data.get(CONF_PROPORTIONAL, OPTIONS_DEFAULTS[CONF_PROPORTIONAL])): str,
                vol.Required(CONF_INTEGRAL, default=self.data.get(CONF_INTEGRAL, OPTIONS_DEFAULTS[CONF_INTEGRAL])): str,
                vol.Required(CONF_DERIVATIVE, default=self.data.get(CONF_DERIVATIVE, OPTIONS_DEFAULTS[CONF_DERIVATIVE])): str
            })
        )

    async def async_step_manufacturer(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            self.data.update(_user_input)
            return await self.async_step_finish()

        coordinator = await self.async_create_coordinator()
        await coordinator.async_setup()

        try:
            manufacturers = ManufacturerFactory.resolve_by_member_id(coordinator.member_id)
            default_manufacturer = manufacturers[0].friendly_name if len(manufacturers) > 0 else -1
        finally:
            await coordinator.async_will_remove_from_hass()

        options = []
        for name in MANUFACTURERS:
            manufacturer = ManufacturerFactory.resolve_by_name(name)
            options.append({"value": name, "label": manufacturer.friendly_name})

        return self.async_show_form(
            last_step=True,
            step_id="manufacturer",
            data_schema=vol.Schema({
                vol.Required(CONF_MANUFACTURER, default=default_manufacturer): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                )
            })
        )

    async def async_step_finish(self, _user_input: dict[str, Any] | None = None):
        if self.config_entry is not None:
            return self.async_update_reload_and_abort(
                data=self.data,
                entry=self.config_entry,
                title=self.data[CONF_NAME],
                reason="reconfigure_successful",
            )

        return self.async_create_entry(
            title=self.data[CONF_NAME],
            data=self.data
        )

    async def async_create_coordinator(self) -> SatDataUpdateCoordinator:
        """Resolve the coordinator by using the factory according to the mode"""
        return SatDataUpdateCoordinatorFactory().resolve(
            hass=self.hass, data=self.data, mode=self.data[CONF_MODE], device=self.data[CONF_DEVICE]
        )

    def _create_mqtt_form(self, step_id: str, default_topic: Optional[str] = None, default_device: Optional[str] = None):
        """Create a common MQTT configuration form."""
        schema = {vol.Required(CONF_NAME, default=DEFAULT_NAME): str}

        if default_topic and not self.data.get(CONF_MQTT_TOPIC):
            schema[vol.Required(CONF_MQTT_TOPIC, default=default_topic)] = str

        if default_device and not self.data.get(CONF_DEVICE):
            schema[vol.Required(CONF_DEVICE, default=default_device)] = str

        return self.async_show_form(
            step_id=step_id,
            last_step=False,
            data_schema=vol.Schema(schema),
        )

    def _enable_overshoot_protection(self, overshoot_protection_value: float):
        """Store the value and enable overshoot protection."""
        self.data[CONF_OVERSHOOT_PROTECTION] = True
        self.data[CONF_MINIMUM_SETPOINT] = overshoot_protection_value


class SatOptionsFlowHandler(config_entries.OptionsFlow):
    """Config flow options handler."""

    def __init__(self, config_entry: ConfigEntry):
        self._config_entry = config_entry
        self._options = dict(config_entry.options)

        self._zones: list[dict[str, Any]] | None = None
        self._zone_editing: str | None = None

    async def async_step_init(self, _user_input: dict[str, Any] | None = None):
        menu_options = ["general", "zones", "system_configuration", "advanced"]

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options
        )

    async def async_step_zones(self, _user_input: dict[str, Any] | None = None):
        """Show all zones, and let the user pick one to change, add or remove zones, or save."""
        if self._zones is None:
            self._zones = [dict(zone) for zone in (await self.get_options())[CONF_ZONES]]

        if _user_input is not None:
            choice = _user_input[ZONE_CHOICE]

            if choice == ZONE_CHOICE_SAVE:
                return await self.update_options({CONF_ZONES: self._zones})

            if choice == ZONE_CHOICE_AREAS:
                return await self.async_step_zone_areas()

            self._zone_editing = choice
            return await self.async_step_zone()

        texts = ZONE_TEXTS["nl" if self.hass.config.language.startswith("nl") else "en"]
        choices = [selector.SelectOptionDict(value=zone[CONF_ZONE_AREA], label=self._zone_label(zone, texts)) for zone in self._zones]
        choices += [
            selector.SelectOptionDict(value=ZONE_CHOICE_AREAS, label=texts["areas"]),
            selector.SelectOptionDict(value=ZONE_CHOICE_SAVE, label=texts["save"]),
        ]

        return self.async_show_form(
            step_id="zones",
            description_placeholders={"overview": "\n".join(self._zone_overview(zone, texts) for zone in self._zones) or texts["none"]},
            data_schema=vol.Schema({
                vol.Required(ZONE_CHOICE, default=ZONE_CHOICE_SAVE): selector.SelectSelector(
                    selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=choices)
                ),
            })
        )

    async def async_step_zone_areas(self, _user_input: dict[str, Any] | None = None):
        """Select the Home Assistant areas whose Danfoss Ally TRVs we control."""
        if _user_input is not None:
            zones = {zone[CONF_ZONE_AREA]: zone for zone in self._zones}

            # Keep the settings of the zones we already had, new zones start with sensible defaults
            self._zones = [zones.get(area_id) or self._new_zone(area_id) for area_id in _user_input.get(CONF_ZONES) or []]
            return await self.async_step_zones()

        return self.async_show_form(
            step_id="zone_areas",
            data_schema=vol.Schema({
                vol.Optional(CONF_ZONES, default=[zone[CONF_ZONE_AREA] for zone in self._zones]): selector.AreaSelector(
                    selector.AreaSelectorConfig(multiple=True, device=[
                        selector.DeviceFilterSelectorConfig(manufacturer=ALLY_MANUFACTURER, model_id=model_id) for model_id in ALLY_MODEL_IDS
                    ] + [selector.DeviceFilterSelectorConfig(manufacturer=ALLY_MANUFACTURER, model=ALLY_MODEL)])
                ),
            })
        )

    async def async_step_zone(self, _user_input: dict[str, Any] | None = None):
        """Change the settings of one zone."""
        zone = next(zone for zone in self._zones if zone[CONF_ZONE_AREA] == self._zone_editing)

        if _user_input is not None:
            zone.update(_user_input)
            return await self.async_step_zones()

        trvs = discover_trvs(self.hass, self._zone_editing)
        settings = {**ZONE_DEFAULTS, **zone}

        return self.async_show_form(
            step_id="zone",
            description_placeholders={
                "zone": self._area_name(self._zone_editing),
                "trvs": ", ".join(trv.climate for trv in trvs) or "-",
            },
            data_schema=vol.Schema({
                vol.Required(CONF_ZONE_MODE, default=settings[CONF_ZONE_MODE]): selector.SelectSelector(
                    selector.SelectSelectorConfig(mode=SelectSelectorMode.LIST, translation_key="zone_mode", options=[ZONE_MODE_INDEPENDENT, ZONE_MODE_TOGETHER])
                ),
                vol.Required(CONF_ZONE_MINIMUM_OPEN, default=settings[CONF_ZONE_MINIMUM_OPEN]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=10, step=1, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_ZONE_MINIMUM_DEMAND, default=settings[CONF_ZONE_MINIMUM_DEMAND]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=500, step=10, unit_of_measurement="%")
                ),
                vol.Required(CONF_ZONE_MAXIMUM_WAIT, default=settings[CONF_ZONE_MAXIMUM_WAIT]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=240, step=5, unit_of_measurement="min")
                ),
            })
        )

    def _new_zone(self, area_id: str) -> dict[str, Any]:
        """Return the settings of a new zone, rooms with more than one TRV most likely want to heat together."""
        mode = ZONE_MODE_TOGETHER if len(discover_trvs(self.hass, area_id)) > 1 else ZONE_MODE_INDEPENDENT
        return {CONF_ZONE_AREA: area_id, **ZONE_DEFAULTS, CONF_ZONE_MODE: mode}

    def _area_name(self, area_id: str) -> str:
        area = area_registry.async_get(self.hass).async_get_area(area_id)
        return area.name if area is not None else area_id

    def _zone_label(self, zone: dict[str, Any], texts: dict[str, str]) -> str:
        """Return the label of a zone in the dropdown, e.g. "Woonkamer: Together, 5 TRVs"."""
        settings = {**ZONE_DEFAULTS, **zone}
        trvs = len(discover_trvs(self.hass, zone[CONF_ZONE_AREA]))

        return f"{self._area_name(zone[CONF_ZONE_AREA])}: {texts[settings[CONF_ZONE_MODE]]}, {trvs} TRV{'s' if trvs != 1 else ''}"

    def _zone_overview(self, zone: dict[str, Any], texts: dict[str, str]) -> str:
        """Return the overview of a zone: its settings and its TRVs."""
        settings = {**ZONE_DEFAULTS, **zone}
        trvs = [trv.climate.removeprefix("climate.") for trv in discover_trvs(self.hass, zone[CONF_ZONE_AREA])]

        line = f"- **{self._area_name(zone[CONF_ZONE_AREA])}**: {texts[settings[CONF_ZONE_MODE]]}"
        if settings[CONF_ZONE_MODE] == ZONE_MODE_TOGETHER:
            line += texts["rules"].format(
                minimum_open=int(settings[CONF_ZONE_MINIMUM_OPEN]),
                minimum_demand=int(settings[CONF_ZONE_MINIMUM_DEMAND]),
                maximum_wait=int(settings[CONF_ZONE_MAXIMUM_WAIT]),
            )

        return line + f"\n  {', '.join(trvs) or texts['no_trvs']}"

    async def async_step_general(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        schema = {}
        options = await self.get_options()

        default_maximum_setpoint = calculate_default_maximum_setpoint(self._config_entry.data.get(CONF_HEATING_SYSTEM))
        maximum_setpoint = float(options.get(CONF_MAXIMUM_SETPOINT, default_maximum_setpoint))

        schema[vol.Required(CONF_PID_CONTROLLER_VERSION, default=str(options[CONF_PID_CONTROLLER_VERSION]))] = selector.SelectSelector(
            selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                selector.SelectOptionDict(value="1", label="Classic Controller"),
                selector.SelectOptionDict(value="2", label="Improved Controller"),
                selector.SelectOptionDict(value="3", label="Adaptive Controller")
            ])
        )

        if options[CONF_DYNAMIC_MINIMUM_SETPOINT]:
            schema[vol.Required(CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION, default=str(options[CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value="1", label="Return Temperature"),
                    selector.SelectOptionDict(value="2", label="Boiler Temperature"),
                ])
            )

        schema[vol.Required(CONF_MAXIMUM_SETPOINT, default=maximum_setpoint)] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=10, max=100, step=1, unit_of_measurement="°C")
        )

        schema[vol.Required(CONF_HEATING_CURVE_COEFFICIENT, default=options[CONF_HEATING_CURVE_COEFFICIENT])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.1, max=12, step=0.1)
        )

        # Kept above DEADBAND, so a held room never feeds the PID integral or the heating curve autotune
        schema[vol.Required(CONF_OVERSHOOT_HOLD, default=options[CONF_OVERSHOOT_HOLD])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.2, max=3.0, step=0.1, unit_of_measurement="°C")
        )

        if options[CONF_AUTOMATIC_GAINS]:
            if int(options[CONF_PID_CONTROLLER_VERSION]) < 3:
                schema[vol.Required(CONF_AUTOMATIC_GAINS_VALUE, default=options[CONF_AUTOMATIC_GAINS_VALUE])] = selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=5, step=0.1)
                )
                schema[vol.Required(CONF_DERIVATIVE_TIME_WEIGHT, default=options[CONF_DERIVATIVE_TIME_WEIGHT])] = selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=6, step=0.1)
                )
        else:
            schema[vol.Required(CONF_PROPORTIONAL, default=options[CONF_PROPORTIONAL])] = str
            schema[vol.Required(CONF_INTEGRAL, default=options[CONF_INTEGRAL])] = str
            schema[vol.Required(CONF_DERIVATIVE, default=options[CONF_DERIVATIVE])] = str

        if options[CONF_DYNAMIC_MINIMUM_SETPOINT]:
            schema[vol.Required(CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR, default=options[CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.1, max=0.5, step=0.1)
            )

        if not options[CONF_AUTOMATIC_DUTY_CYCLE]:
            schema[vol.Required(CONF_DUTY_CYCLE, default=options[CONF_DUTY_CYCLE])] = selector.TimeSelector()

        return self.async_show_form(step_id="general", data_schema=vol.Schema(schema))

    async def async_step_system_configuration(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema: dict[Marker, Any] = {
            vol.Required(CONF_AUTOMATIC_DUTY_CYCLE, default=options[CONF_AUTOMATIC_DUTY_CYCLE]): bool,
        }

        if options.get(CONF_HEATING_SYSTEM) == HEATING_SYSTEM_HEAT_PUMP:
            schema[vol.Required(CONF_CYCLES_PER_HOUR, default=str(options[CONF_CYCLES_PER_HOUR]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value="2", label="Normal (2x per hour)"),
                    selector.SelectOptionDict(value="3", label="High (3x per hour)"),
                ])
            )

        if options.get(CONF_HEATING_SYSTEM) == HEATING_SYSTEM_RADIATORS:
            schema[vol.Required(CONF_CYCLES_PER_HOUR, default=str(options[CONF_CYCLES_PER_HOUR]))] = selector.SelectSelector(
                selector.SelectSelectorConfig(mode=SelectSelectorMode.DROPDOWN, options=[
                    selector.SelectOptionDict(value="3", label="Normal (3x per hour)"),
                    selector.SelectOptionDict(value="4", label="High (4x per hour)"),
                ])
            )

        schema[vol.Required(CONF_SENSOR_MAX_VALUE_AGE, default=options[CONF_SENSOR_MAX_VALUE_AGE])] = selector.TimeSelector()

        return self.async_show_form(
            step_id="system_configuration",
            data_schema=vol.Schema(schema)
        )

    async def async_step_advanced(self, _user_input: dict[str, Any] | None = None):
        if _user_input is not None:
            return await self.update_options(_user_input)

        options = await self.get_options()

        schema: dict[Marker, Any] = {
            vol.Required(CONF_SIMULATION, default=options[CONF_SIMULATION]): bool,
            vol.Required(CONF_DYNAMIC_MINIMUM_SETPOINT, default=options[CONF_DYNAMIC_MINIMUM_SETPOINT]): bool,
        }

        if self._config_entry.data.get(CONF_MODE) in [MODE_MQTT_OPENTHERM, MODE_SERIAL, MODE_SIMULATOR]:
            schema[vol.Required(CONF_FORCE_PULSE_WIDTH_MODULATION, default=options[CONF_FORCE_PULSE_WIDTH_MODULATION])] = bool

            schema[vol.Required(CONF_MINIMUM_CONSUMPTION, default=options[CONF_MINIMUM_CONSUMPTION])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=8, step=0.1)
            )

            schema[vol.Required(CONF_MAXIMUM_CONSUMPTION, default=options[CONF_MAXIMUM_CONSUMPTION])] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=8, step=0.1)
            )

        schema[vol.Required(CONF_MAXIMUM_RELATIVE_MODULATION, default=options[CONF_MAXIMUM_RELATIVE_MODULATION])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=100, step=1)
        )

        schema[vol.Required(CONF_SAMPLE_TIME, default=options[CONF_SAMPLE_TIME])] = selector.TimeSelector()

        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(schema)
        )

    async def update_options(self, _user_input):
        self._options.update(_user_input)
        return self.async_create_entry(title=self._config_entry.data[CONF_NAME], data=self._options)

    async def get_options(self):
        options = OPTIONS_DEFAULTS.copy()
        options.update(self._options)

        return options

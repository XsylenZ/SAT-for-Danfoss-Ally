"""The tests for the zones of Danfoss Ally TRVs."""
import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.template import DOMAIN as TEMPLATE_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ally_otgw.switch import SatHeating
from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.fake import SatFakeCoordinator
from custom_components.ally_otgw.zones import Trv, discover_trvs
from tests.helpers import add_trv, move_trv
from tests.test_area import create_areas, create_state

TOGETHER = {CONF_ZONE_MODE: ZONE_MODE_TOGETHER, CONF_ZONE_MINIMUM_OPEN: 2, CONF_ZONE_MINIMUM_DEMAND: 100, CONF_ZONE_MAXIMUM_WAIT: 30}

LIVING_ROOM = ["climate.tv_hoek_r", "climate.tv_hoek_l", "climate.eetkamer_r"]


def living_room(heating: list[str], openings: dict[str, float] | None = None, **settings):
    states = {entity_id: create_state(19.0, 20.0, HVACAction.HEATING if entity_id in heating else HVACAction.IDLE) for entity_id in LIVING_ROOM}
    areas = create_areas(states, {"woonkamer": {**TOGETHER, **settings, "trvs": LIVING_ROOM}}, openings)

    return areas, states, areas.zones[0]


def test_together_waits_for_a_single_trv_with_a_small_opening() -> None:
    areas, _, zone = living_room(["climate.tv_hoek_r"], {"climate.tv_hoek_r": 20})

    assert not zone.calling
    assert not areas.demand
    assert len(areas.errors) == 0


def test_together_calls_when_enough_trvs_are_heating() -> None:
    areas, _, zone = living_room(["climate.tv_hoek_r", "climate.tv_hoek_l"], {"climate.tv_hoek_r": 20, "climate.tv_hoek_l": 10})

    assert zone.calling
    assert areas.demand
    assert {error.entity_id for error in areas.errors} == {"climate.tv_hoek_r", "climate.tv_hoek_l"}


def test_together_calls_when_the_valves_are_open_enough() -> None:
    _, _, zone = living_room(["climate.tv_hoek_r"], {"climate.tv_hoek_r": 120})

    assert zone.calling
    assert zone.opening == 120


def test_together_ignores_the_opening_when_disabled() -> None:
    _, _, zone = living_room(["climate.tv_hoek_r"], {"climate.tv_hoek_r": 120}, **{CONF_ZONE_MINIMUM_DEMAND: 0})

    assert not zone.calling


def test_together_stops_waiting_after_the_maximum_wait() -> None:
    areas, _, zone = living_room(["climate.tv_hoek_r"], {"climate.tv_hoek_r": 20})
    assert not zone.calling

    zone._waiting_since -= 30 * 60 + 1
    areas.update()

    assert zone.calling


def test_together_keeps_calling_until_all_trvs_are_satisfied() -> None:
    areas, states, zone = living_room(["climate.tv_hoek_r", "climate.tv_hoek_l"])
    assert zone.calling

    # One TRV is satisfied, the other still wants heat, so the zone doesn't flap
    states["climate.tv_hoek_r"] = create_state(20.5, 20.0, HVACAction.IDLE)
    areas.update()
    assert zone.calling

    # All TRVs are satisfied
    states["climate.tv_hoek_l"] = create_state(20.5, 20.0, HVACAction.IDLE)
    areas.update()
    assert not zone.calling

    # A single TRV starts waiting again from scratch
    states["climate.tv_hoek_l"] = create_state(19.0, 20.0, HVACAction.HEATING)
    areas.update()
    assert not zone.calling


def test_independent_zone_calls_for_a_single_trv() -> None:
    areas = create_areas(
        {"climate.werkkamer": create_state(19.5, 20.0, HVACAction.HEATING)},
        {"werkkamer": {CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, "trvs": ["climate.werkkamer"]}},
        {"climate.werkkamer": 5},
    )

    assert areas.zones[0].calling
    assert areas.demand


def test_only_calling_zones_drive_the_boiler() -> None:
    areas = create_areas(
        {
            "climate.tv_hoek_r": create_state(18.0, 20.0, HVACAction.HEATING),
            "climate.tv_hoek_l": create_state(20.5, 20.0, HVACAction.IDLE),
            "climate.werkkamer": create_state(19.5, 20.0, HVACAction.HEATING),
        },
        {
            "woonkamer": {**TOGETHER, "trvs": ["climate.tv_hoek_r", "climate.tv_hoek_l"]},
            "werkkamer": {CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, "trvs": ["climate.werkkamer"]},
        },
        {"climate.tv_hoek_r": 20},
    )

    # The living room waits, so its larger error must not push the flow temperature up
    assert areas.demand
    assert [error.entity_id for error in areas.errors] == ["climate.werkkamer"]


async def test_discover_only_danfoss_ally_trvs(hass: HomeAssistant) -> None:
    await add_trv(hass, "Woonkamer", "tv_hoek_r")
    await add_trv(hass, "Woonkamer", "eetkamer_r", model_id=None)
    await add_trv(hass, "Woonkamer", "airco", manufacturer="Daikin", model="FTXM", model_id="FTXM25")
    await add_trv(hass, "Werkkamer", "werkkamer")

    assert discover_trvs(hass, "woonkamer") == [
        Trv("climate.eetkamer_r", "sensor.eetkamer_r_pi_heating_demand", "sensor.eetkamer_r_load_estimate", "number.eetkamer_r_load_room_mean", "woonkamer"),
        Trv("climate.tv_hoek_r", "sensor.tv_hoek_r_pi_heating_demand", "sensor.tv_hoek_r_load_estimate", "number.tv_hoek_r_load_room_mean", "woonkamer"),
    ]
    assert [trv.climate for trv in discover_trvs(hass, "werkkamer")] == ["climate.werkkamer"]
    assert [trv.climate for trv in discover_trvs(hass)] == ["climate.eetkamer_r", "climate.tv_hoek_r", "climate.werkkamer"]


async def test_discover_uses_the_area_of_the_entity_first(hass: HomeAssistant) -> None:
    await add_trv(hass, "Woonkamer", "speelhoek")
    await add_trv(hass, "Werkkamer", "werkkamer")

    entity_registry.async_get(hass).async_update_entity("climate.speelhoek", area_id="werkkamer")

    assert discover_trvs(hass, "woonkamer") == []
    assert [trv.climate for trv in discover_trvs(hass, "werkkamer")] == ["climate.speelhoek", "climate.werkkamer"]


ZONES = [
    {CONF_ZONE_AREA: "woonkamer", **TOGETHER},
    {CONF_ZONE_AREA: "werkkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT},
]


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
                CONF_ZONES: ZONES,
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
async def test_zones_follow_trvs_that_are_added_moved_and_removed(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, coordinator: SatFakeCoordinator) -> None:
    await heating.async_turn_on()

    # TRVs added after the integration started are picked up
    await add_trv(hass, "Woonkamer", "tv_hoek_r")
    await add_trv(hass, "Woonkamer", "tv_hoek_l")
    await add_trv(hass, "Werkkamer", "werkkamer")

    zones = heating.extra_state_attributes["zones"]
    assert zones["woonkamer"]["name"] == "Woonkamer"
    assert zones["woonkamer"]["trvs"] == ["climate.tv_hoek_l", "climate.tv_hoek_r"]
    assert zones["werkkamer"]["trvs"] == ["climate.werkkamer"]

    # And their state changes are followed
    hass.states.async_set("climate.werkkamer", HVACMode.HEAT, {"current_temperature": 19.0, "temperature": 20.0, "hvac_action": "heating", "hvac_modes": [HVACMode.HEAT]})
    await hass.async_block_till_done()
    await heating.async_control_heating_loop()

    assert heating.extra_state_attributes["zones"]["werkkamer"]["calling"]
    assert heating.heat_demand

    # Moving a TRV to another area moves it to that zone, or out of all zones
    await move_trv(hass, "tv_hoek_l", "Werkkamer")
    await move_trv(hass, "werkkamer", "Zolder")

    zones = heating.extra_state_attributes["zones"]
    assert zones["woonkamer"]["trvs"] == ["climate.tv_hoek_r"]
    assert zones["werkkamer"]["trvs"] == ["climate.tv_hoek_l"]
    assert "climate.werkkamer" not in heating.areas.items()


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {CONF_MODE: MODE_FAKE, CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS},
            {CONF_ZONES: [{CONF_ZONE_AREA: "woonkamer", **TOGETHER}]},
            {TEMPLATE_DOMAIN: [{SENSOR_DOMAIN: [{"name": "test_outside_sensor", "state": "{{ 9.9 | float }}"}]}]},
    )],
])
async def test_overview_of_all_trvs(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating, caplog: pytest.LogCaptureFixture) -> None:
    await add_trv(hass, "Woonkamer", "tv_hoek_r")
    await add_trv(hass, "Werkkamer", "werkkamer")
    await add_trv(hass, "Woonkamer", "airco", manufacturer="Daikin", model="FTXM", model_id="FTXM25")

    hass.states.async_set("climate.tv_hoek_r", HVACMode.HEAT, {"current_temperature": 19.5, "temperature": 20.0, "hvac_action": "heating"})
    hass.states.async_set("sensor.tv_hoek_r_pi_heating_demand", "35")
    await hass.async_block_till_done()

    # A TRV outside of every zone won't call for heat, the user should know
    assert "not in any zone, so they won't call for heat: climate.werkkamer" in caplog.text

    heating.async_write_ha_state()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.danfoss_ally_trvs_test")
    assert state.state == "2"
    assert state.attributes["in_a_zone"] == 1
    assert state.attributes["not_in_a_zone"] == ["climate.werkkamer"]
    assert state.attributes["trvs"] == [
        {
            "entity_id": "climate.tv_hoek_r", "name": "tv hoek r", "area": "Woonkamer", "zone": "Woonkamer",
            "status": "heating", "current_temperature": 19.5, "target_temperature": 20.0, "opening": 35.0,
        },
        {
            "entity_id": "climate.werkkamer", "name": "climate.werkkamer", "area": "Werkkamer", "zone": None,
            "status": "unavailable", "current_temperature": None, "target_temperature": None, "opening": 0.0,
        },
    ]


@pytest.mark.parametrize(*[
    "domains, data, options, config",
    [(
            [(TEMPLATE_DOMAIN, 1)],
            {
                CONF_MODE: MODE_FAKE,
                CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,
            },
            {},
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
async def test_configure_zones(hass: HomeAssistant, entry: MockConfigEntry, heating: SatHeating) -> None:
    await add_trv(hass, "Woonkamer", "tv_hoek_r")
    await add_trv(hass, "Woonkamer", "tv_hoek_l")
    await add_trv(hass, "Werkkamer", "werkkamer")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "zones" in result["menu_options"]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "zones"})
    assert result["step_id"] == "zones"

    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_ZONES: ["woonkamer", "werkkamer"]})
    assert result["step_id"] == "zone"
    assert result["description_placeholders"] == {"zone": "Woonkamer", "trvs": "climate.tv_hoek_l, climate.tv_hoek_r"}

    # A room with more than one TRV suggests heating together
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults[CONF_ZONE_MODE] == ZONE_MODE_TOGETHER

    result = await hass.config_entries.options.async_configure(result["flow_id"], {**defaults, CONF_ZONE_MINIMUM_OPEN: 3})
    assert result["step_id"] == "zone"
    assert result["description_placeholders"] == {"zone": "Werkkamer", "trvs": "climate.werkkamer"}

    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults[CONF_ZONE_MODE] == ZONE_MODE_INDEPENDENT

    result = await hass.config_entries.options.async_configure(result["flow_id"], defaults)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.options[CONF_ZONES] == [
        {CONF_ZONE_AREA: "woonkamer", **TOGETHER, CONF_ZONE_MINIMUM_OPEN: 3},
        {CONF_ZONE_AREA: "werkkamer", CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, CONF_ZONE_MINIMUM_OPEN: 2, CONF_ZONE_MINIMUM_DEMAND: 100, CONF_ZONE_MAXIMUM_WAIT: 30},
    ]

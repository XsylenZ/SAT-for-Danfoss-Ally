"""The tests for the area error gating."""
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt

from custom_components.ally_otgw.area import Area, Areas
from custom_components.ally_otgw.const import *
from custom_components.ally_otgw.zones import Trv
from tests.const import DEFAULT_USER_DATA

ENTITY_ID = "climate.radiator_test"


def create_area(state: State | None) -> Area:
    area = Area(MappingProxyType(DEFAULT_USER_DATA), MappingProxyType(OPTIONS_DEFAULTS), ENTITY_ID)
    area._hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: state))

    return area


def create_state(current: float, target: float, hvac_action: str | None, age: float = 0, state: str = HVACMode.HEAT) -> State:
    attributes = {"current_temperature": current, "temperature": target}
    if hvac_action is not None:
        attributes["hvac_action"] = hvac_action

    timestamp = dt.utcnow() - timedelta(seconds=age)
    return State(ENTITY_ID, state, attributes, last_changed=timestamp, last_updated=timestamp, last_reported=timestamp)


@pytest.mark.parametrize("current, target, hvac_action, expected", [
    # Not calling for heat: excluded, regardless of the temperature difference
    (19.0, 20.0, HVACAction.IDLE, None),
    (19.9, 20.0, HVACAction.IDLE, None),
    (19.0, 20.0, HVACAction.OFF, None),
    # Calling for heat: the real error when it is larger than the hold
    (19.0, 20.0, HVACAction.HEATING, 1.0),
    # Calling for heat: held at the floor around and above the target
    (19.9, 20.0, HVACAction.HEATING, OPTIONS_DEFAULTS[CONF_OVERSHOOT_HOLD]),
    (20.0, 20.0, HVACAction.HEATING, OPTIONS_DEFAULTS[CONF_OVERSHOOT_HOLD]),
    (20.6, 20.0, HVACAction.HEATING, OPTIONS_DEFAULTS[CONF_OVERSHOOT_HOLD]),
    # No action reported: plain error, like before
    (19.9, 20.0, None, 0.1),
    (20.6, 20.0, None, -0.6),
])
def test_error_gating(current, target, hvac_action, expected) -> None:
    error = create_area(create_state(current, target, hvac_action)).error

    if expected is None:
        assert error is None
    else:
        assert error.entity_id == ENTITY_ID
        assert error.value == expected


@pytest.mark.parametrize("current, target, hvac_action", [
    (18.0, 20.0, HVACAction.HEATING),
    (18.0, 20.0, HVACAction.IDLE),
    (18.0, 20.0, None),
    (20.6, 20.0, HVACAction.HEATING),
])
def test_stale_is_excluded(current, target, hvac_action) -> None:
    area = create_area(create_state(current, target, hvac_action, age=AREA_STATE_MAX_AGE + 60))

    assert area.stale
    assert not area.available
    assert area.error is None


def test_recent_state_is_not_stale() -> None:
    area = create_area(create_state(20.6, 20.0, HVACAction.HEATING, age=AREA_STATE_MAX_AGE - 60))

    assert not area.stale
    assert area.error.value == OPTIONS_DEFAULTS[CONF_OVERSHOOT_HOLD]


def test_unavailable_is_excluded() -> None:
    area = create_area(create_state(19.0, 20.0, HVACAction.HEATING, state=STATE_UNAVAILABLE))

    assert not area.stale
    assert area.error is None


def test_hold_stays_outside_pid_deadband() -> None:
    assert OPTIONS_DEFAULTS[CONF_OVERSHOOT_HOLD] > DEADBAND


def test_hold_is_configurable() -> None:
    options = {**OPTIONS_DEFAULTS, CONF_OVERSHOOT_HOLD: 1.2}
    area = Area(MappingProxyType(DEFAULT_USER_DATA), MappingProxyType(options), ENTITY_ID)
    area._hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: create_state(20.6, 20.0, HVACAction.HEATING)))

    assert area.error.value == 1.2


def create_areas(states: dict[str, State | None], zones: dict[str, dict] | None = None, openings: dict[str, float | None] | None = None, valves: bool = True) -> Areas:
    """Create areas for the given TRV states, by default all in one independent zone, with their valves fully open."""
    zones = zones or {"zone": {CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, "trvs": list(states)}}
    openings = {**{entity_id: 100 for entity_id in states}, **(openings or {})}
    sensors = {f"sensor.{entity_id.split('.')[1]}_pi_heating_demand": State("sensor.x", str(opening)) for entity_id, opening in openings.items() if opening is not None}

    def discover(_hass, area_id: str) -> list[Trv]:
        return [Trv(entity_id, pi_heating_demand=f"sensor.{entity_id.split('.')[1]}_pi_heating_demand" if valves else None) for entity_id in zones[area_id]["trvs"]]

    settings = [{CONF_ZONE_AREA: area_id, **{key: value for key, value in zone.items() if key != "trvs"}} for area_id, zone in zones.items()]
    areas = Areas(MappingProxyType(DEFAULT_USER_DATA), MappingProxyType({**OPTIONS_DEFAULTS, CONF_ZONES: settings}), discover=discover)

    areas._hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: states.get(entity_id) or sensors.get(entity_id)))
    areas.refresh()
    areas.update()

    return areas


def test_demand_when_a_room_is_heating() -> None:
    assert create_areas({
        "climate.radiator_1": create_state(20.5, 20.0, HVACAction.IDLE),
        "climate.radiator_2": create_state(19.5, 20.0, HVACAction.HEATING),
    }).demand


def test_no_demand_when_all_rooms_are_idle() -> None:
    assert not create_areas({
        "climate.radiator_1": create_state(19.0, 20.0, HVACAction.IDLE),
        "climate.radiator_2": create_state(18.0, 20.0, HVACAction.IDLE),
    }).demand


def test_no_demand_when_idle_and_unavailable() -> None:
    assert not create_areas({
        "climate.radiator_1": create_state(19.0, 20.0, HVACAction.IDLE),
        "climate.radiator_2": None,
    }).demand


def test_unknown_demand_when_no_room_is_available() -> None:
    assert create_areas({
        "climate.radiator_1": None,
        "climate.radiator_2": create_state(19.0, 20.0, HVACAction.HEATING, state=STATE_UNAVAILABLE),
    }).demand is None


def test_stale_room_never_demands() -> None:
    # A TRV frozen while calling for heat in a cold room, next to a healthy idle one
    assert not create_areas({
        "climate.radiator_1": create_state(18.0, 20.0, HVACAction.HEATING, age=AREA_STATE_MAX_AGE + 60),
        "climate.radiator_2": create_state(20.5, 20.0, HVACAction.IDLE),
    }).demand


def test_unknown_demand_when_all_rooms_are_stale_or_unavailable() -> None:
    # Nothing can tell us anything anymore, so the main room has to decide
    assert create_areas({
        "climate.radiator_1": create_state(18.0, 20.0, HVACAction.HEATING, age=AREA_STATE_MAX_AGE + 60),
        "climate.radiator_2": None,
    }).demand is None


def create_area_with_opening(current: float, target: float, opening: str | None, hold: float = 1.2) -> Area:
    """An area for a heating TRV whose valve is open this far."""
    states = {ENTITY_ID: create_state(current, target, HVACAction.HEATING), "sensor.pi_heating_demand": State("sensor.pi_heating_demand", opening) if opening is not None else None}

    area = Area(MappingProxyType(DEFAULT_USER_DATA), MappingProxyType({**OPTIONS_DEFAULTS, CONF_OVERSHOOT_HOLD: hold}), ENTITY_ID, "sensor.pi_heating_demand")
    area._hass = SimpleNamespace(states=SimpleNamespace(get=states.get))

    return area


@pytest.mark.parametrize("opening, hold", [
    ("100", 1.2),
    ("95", 1.14),
    ("50", 0.6),
    # Nearly closed, but never below the minimum, which keeps it out of the PID deadband
    ("12", AREA_MINIMUM_OVERSHOOT_HOLD),
    ("0", AREA_MINIMUM_OVERSHOOT_HOLD),
    # Without a usable valve opening, the full hold like before
    (STATE_UNAVAILABLE, 1.2),
    ("unknown", 1.2),
    (None, 1.2),
])
def test_overshoot_hold_follows_the_valve_opening(opening, hold) -> None:
    area = create_area_with_opening(22.4, 22.5, opening)

    assert area.overshoot_hold == hold
    assert area.error.value == hold


def test_a_real_error_larger_than_the_hold_still_counts() -> None:
    # The room is far below its target, so the nearly closed valve doesn't lower what it asks for
    assert create_area_with_opening(20.0, 22.5, "12").error.value == 2.5


def test_the_room_that_needs_the_most_decides() -> None:
    areas = create_areas(
        {
            "climate.woonkamer": create_state(20.0, 20.5, HVACAction.HEATING),
            "climate.werkkamer": create_state(22.1, 22.5, HVACAction.HEATING),
        },
        {
            "woonkamer": {CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, "trvs": ["climate.woonkamer"]},
            "werkkamer": {CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT, "trvs": ["climate.werkkamer"]},
        },
        {"climate.woonkamer": 95, "climate.werkkamer": 12},
    )

    errors = {error.entity_id: error.value for error in areas.errors}

    # The wide open valve keeps almost the full hold and drives the boiler, the nearly closed one doesn't ask for heat at all
    assert errors == {"climate.woonkamer": 0.5}
    assert max(areas.errors, key=lambda error: error.value).entity_id == "climate.woonkamer"

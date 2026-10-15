"""The tests for the area error gating."""
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt

from custom_components.sat.area import Area, Areas
from custom_components.sat.const import *
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


def create_areas(states: dict[str, State | None]) -> Areas:
    areas = Areas(MappingProxyType({**DEFAULT_USER_DATA, CONF_ROOMS: list(states)}), MappingProxyType(OPTIONS_DEFAULTS))
    for area in areas._areas:
        area._hass = SimpleNamespace(states=SimpleNamespace(get=lambda entity_id: states[entity_id]))

    return areas


def test_demand_when_a_room_is_heating() -> None:
    assert create_areas({
        "climate.radiator_1": create_state(20.5, 20.0, HVACAction.IDLE),
        "climate.radiator_2": create_state(20.5, 20.0, HVACAction.HEATING),
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

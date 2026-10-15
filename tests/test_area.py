"""The tests for the area error gating."""
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.util import dt

from custom_components.sat.area import Area
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


@pytest.mark.parametrize("current, target, hvac_action, expected", [
    (19.9, 20.0, HVACAction.IDLE, 0.1),
    (20.6, 20.0, HVACAction.HEATING, -0.6),
])
def test_stale_falls_back_to_plain_error(current, target, hvac_action, expected) -> None:
    area = create_area(create_state(current, target, hvac_action, age=AREA_STATE_MAX_AGE + 60))

    assert area.stale
    assert area.error.value == expected


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

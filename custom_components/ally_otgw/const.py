# Base component constants
from __future__ import annotations

from enum import Enum

NAME = "Danfoss Ally OTGW"
DOMAIN = "ally_otgw"
CLIMATE = "climate"
COORDINATOR = "coordinator"
CONFIG_STORE = "config_store"

MODE_FAKE = "fake"
MODE_MQTT_EMS = "mqtt_ems"
MODE_MQTT_OPENTHERM = "mqtt_opentherm"
MODE_SWITCH = "switch"
MODE_SERIAL = "serial"
MODE_ESPHOME = "esphome"
MODE_SIMULATOR = "simulator"

DEADBAND = 0.1
BOILER_DEADBAND = 2

# Room demand gating (rooms only contribute an error while their thermostat reports it is heating)
AREA_STATE_MAX_AGE = 3600

# The overshoot hold follows how far the valve is open, but stays above DEADBAND so it never feeds the PID integral
AREA_MINIMUM_OVERSHOOT_HOLD = 0.2

# Once no room is calling for heat, a running burn may finish, but never for longer than this (in seconds)
DEMAND_BURN_FINISH_TIMEOUT = 600

# Frost guard: when none of the TRVs is available, heat for a temperature below an outside temperature
CONF_FROST_GUARD_TEMPERATURE = "frost_guard_temperature"
CONF_FROST_GUARD_OUTSIDE_TEMPERATURE = "frost_guard_outside_temperature"
FROST_GUARD_HYSTERESIS = 1.0

# Without the outside temperature, keep heating on the last known value for a while, and then on an assumed one
CONF_OUTSIDE_FALLBACK = "outside_temperature_fallback"
CONF_OUTSIDE_FALLBACK_HOURS = "outside_temperature_fallback_hours"
CONF_OUTSIDE_FALLBACK_TEMPERATURE = "outside_temperature_fallback_temperature"

OUTSIDE_SOURCE_SENSOR = "sensor"

# Circulate the warm water that is left to TRVs that still want a little heat: pump on, burner off
CONF_CIRCULATE = "circulate_residual_heat"
CONF_CIRCULATE_MINIMUM_TEMPERATURE = "circulate_minimum_temperature"
CONF_CIRCULATE_MAXIMUM_MINUTES = "circulate_maximum_minutes"
OUTSIDE_SOURCE_LAST_KNOWN = "last_known"
OUTSIDE_SOURCE_ASSUMED = "assumed"

HEATER_STARTUP_TIMEFRAME = 180

COLD_SETPOINT = 28.2
MINIMUM_SETPOINT = 10
MAXIMUM_SETPOINT = 65
MINIMUM_RELATIVE_MODULATION = 0
MAXIMUM_RELATIVE_MODULATION = 100

MAX_BOILER_TEMPERATURE_AGE = 60

# Configuration and options
CONF_MODE = "mode"
CONF_NAME = "name"
CONF_DEVICE = "device"
CONF_THERMOSTAT = "thermostat"
CONF_MANUFACTURER = "manufacturer"
CONF_CYCLES_PER_HOUR = "cycles_per_hour"
CONF_SIMULATED_HEATING = "simulated_heating"
CONF_SIMULATED_COOLING = "simulated_cooling"
CONF_SIMULATED_WARMING_UP = "simulated_warming_up"
CONF_MINIMUM_SETPOINT = "minimum_setpoint"
CONF_MAXIMUM_SETPOINT = "maximum_setpoint"
CONF_MAXIMUM_RELATIVE_MODULATION = "maximum_relative_modulation"
CONF_MQTT_TOPIC = "mqtt_topic"
CONF_RADIATORS = "main_climates"
CONF_WINDOW_SENSORS = "window_sensors"
CONF_PUSH_SETPOINT_TO_THERMOSTAT = "push_setpoint_to_thermostat"
CONF_WINDOW_MINIMUM_OPEN_TIME = "window_minimum_open_time"
CONF_THERMAL_COMFORT = "thermal_comfort"
CONF_SIMULATION = "simulation"
CONF_INTEGRAL = "integral"
CONF_DERIVATIVE = "derivative"
CONF_PROPORTIONAL = "proportional"
CONF_DUTY_CYCLE = "duty_cycle"
CONF_SAMPLE_TIME = "sample_time"
CONF_AUTOMATIC_GAINS = "automatic_gains"
CONF_AUTOMATIC_DUTY_CYCLE = "automatic_duty_cycle"
CONF_AUTOMATIC_GAINS_VALUE = "automatic_gains_value"
CONF_DERIVATIVE_TIME_WEIGHT = "derivative_time_weight"
CONF_CLIMATE_VALVE_OFFSET = "climate_valve_offset"
CONF_SENSOR_MAX_VALUE_AGE = "sensor_max_value_age"
CONF_OVERSHOOT_PROTECTION = "overshoot_protection"
CONF_SYNC_CLIMATES_WITH_MODE = "sync_climates_with_mode"
CONF_SYNC_CLIMATES_WITH_PRESET = "sync_climates_with_preset"
CONF_FORCE_PULSE_WIDTH_MODULATION = "force_pulse_width_modulation"
CONF_TARGET_TEMPERATURE_STEP = "target_temperature_step"
CONF_INSIDE_SENSOR_ENTITY_ID = "inside_sensor_entity_id"
CONF_OUTSIDE_SENSOR_ENTITY_ID = "outside_sensor_entity_id"
CONF_HUMIDITY_SENSOR_ENTITY_ID = "humidity_sensor_entity_id"

CONF_HEATING_MODE = "heating_mode"
CONF_HEATING_SYSTEM = "heating_system"
CONF_HEATING_CURVE_COEFFICIENT = "heating_curve_coefficient"
CONF_OVERSHOOT_HOLD = "overshoot_hold"

# Zones: Home Assistant areas whose Danfoss Ally TRVs are controlled together
CONF_ZONES = "zones"
CONF_ZONE_AREA = "area_id"
CONF_ZONE_MODE = "mode"
CONF_ZONE_MINIMUM_OPEN = "minimum_open"
CONF_ZONE_MINIMUM_DEMAND = "minimum_demand"
CONF_ZONE_MAXIMUM_WAIT = "maximum_wait"
CONF_ZONE_LOAD_BALANCING = "load_balancing"
CONF_ZONE_MINIMUM_OPENING = "minimum_opening"

# Once a TRV counts as asking for heat, it keeps counting until its valve is this much below the minimum opening
ZONE_MINIMUM_OPENING_HYSTERESIS = 5

# Load balancing: the TRVs of a zone share the mean of their load estimates, written every interval (in minutes)
CONF_LOAD_BALANCING_INTERVAL = "load_balancing_interval"
LOAD_UNDEFINED = -8000

ZONE_MODE_INDEPENDENT = "independent"
ZONE_MODE_TOGETHER = "together"

ZONE_DEFAULTS = {
    CONF_ZONE_MODE: ZONE_MODE_INDEPENDENT,
    CONF_ZONE_MINIMUM_OPEN: 2,
    CONF_ZONE_MINIMUM_DEMAND: 100,
    CONF_ZONE_MAXIMUM_WAIT: 30,
    CONF_ZONE_LOAD_BALANCING: False,
    CONF_ZONE_MINIMUM_OPENING: 20,
}

# The radiator thermostats this integration works with
ALLY_MANUFACTURER = "Danfoss"
ALLY_MODEL = "Ally thermostat"
ALLY_MODEL_IDS = ("014G2461",)

CONF_PID_CONTROLLER_VERSION = "pid_controller_version"

CONF_DYNAMIC_MINIMUM_SETPOINT = "dynamic_minimum_setpoint"
CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION = "dynamic_minimum_setpoint_version"
CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR = "minimum_setpoint_adjustment_factor"

CONF_MINIMUM_CONSUMPTION = "minimum_consumption"
CONF_MAXIMUM_CONSUMPTION = "maximum_consumption"

CONF_AWAY_TEMPERATURE = "away_temperature"
CONF_HOME_TEMPERATURE = "home_temperature"
CONF_SLEEP_TEMPERATURE = "sleep_temperature"
CONF_COMFORT_TEMPERATURE = "comfort_temperature"
CONF_ACTIVITY_TEMPERATURE = "activity_temperature"

HEATING_SYSTEM_UNKNOWN = "unknown"
HEATING_SYSTEM_HEAT_PUMP = "heat_pump"
HEATING_SYSTEM_RADIATORS = "radiators"
HEATING_SYSTEM_UNDERFLOOR = "underfloor"

HEATING_MODE_ECO = "eco"
HEATING_MODE_COMFORT = "comfort"

OPTIONS_DEFAULTS = {
    CONF_PROPORTIONAL: "45",
    CONF_INTEGRAL: "0",
    CONF_DERIVATIVE: "6000",

    CONF_CYCLES_PER_HOUR: 4,
    CONF_AUTOMATIC_GAINS: True,
    CONF_AUTOMATIC_DUTY_CYCLE: True,
    CONF_AUTOMATIC_GAINS_VALUE: 2.0,
    CONF_DERIVATIVE_TIME_WEIGHT: 2.5,
    CONF_OVERSHOOT_PROTECTION: False,
    CONF_DYNAMIC_MINIMUM_SETPOINT: False,
    CONF_MINIMUM_SETPOINT_ADJUSTMENT_FACTOR: 0.2,

    CONF_RADIATORS: [],

    CONF_SIMULATION: False,
    CONF_WINDOW_SENSORS: [],
    CONF_THERMAL_COMFORT: False,
    CONF_HUMIDITY_SENSOR_ENTITY_ID: None,
    CONF_PUSH_SETPOINT_TO_THERMOSTAT: False,
    CONF_SYNC_CLIMATES_WITH_MODE: True,
    CONF_SYNC_CLIMATES_WITH_PRESET: False,

    CONF_SIMULATED_HEATING: 20,
    CONF_SIMULATED_COOLING: 5,

    CONF_MINIMUM_SETPOINT: 10,
    CONF_MAXIMUM_RELATIVE_MODULATION: 100,
    CONF_FORCE_PULSE_WIDTH_MODULATION: False,

    CONF_MINIMUM_CONSUMPTION: 0,
    CONF_MAXIMUM_CONSUMPTION: 0,

    CONF_DUTY_CYCLE: "00:13:00",
    CONF_SAMPLE_TIME: "00:01:00",
    CONF_CLIMATE_VALVE_OFFSET: 0,
    CONF_TARGET_TEMPERATURE_STEP: 0.5,
    CONF_SENSOR_MAX_VALUE_AGE: "06:00:00",
    CONF_SIMULATED_WARMING_UP: "00:00:15",
    CONF_WINDOW_MINIMUM_OPEN_TIME: "00:00:15",

    CONF_ACTIVITY_TEMPERATURE: 10,
    CONF_AWAY_TEMPERATURE: 10,
    CONF_HOME_TEMPERATURE: 18,
    CONF_SLEEP_TEMPERATURE: 15,
    CONF_COMFORT_TEMPERATURE: 20,

    CONF_HEATING_CURVE_COEFFICIENT: 2.0,
    CONF_OVERSHOOT_HOLD: 0.5,
    CONF_ZONES: [],
    CONF_LOAD_BALANCING_INTERVAL: 15,
    CONF_FROST_GUARD_TEMPERATURE: 10.0,
    CONF_FROST_GUARD_OUTSIDE_TEMPERATURE: 3.0,
    CONF_OUTSIDE_FALLBACK: True,
    CONF_CIRCULATE: False,
    CONF_CIRCULATE_MINIMUM_TEMPERATURE: 35,
    CONF_CIRCULATE_MAXIMUM_MINUTES: 15,
    CONF_OUTSIDE_FALLBACK_HOURS: 6,
    CONF_OUTSIDE_FALLBACK_TEMPERATURE: 5.0,
    CONF_HEATING_MODE: HEATING_MODE_COMFORT,
    CONF_HEATING_SYSTEM: HEATING_SYSTEM_RADIATORS,

    CONF_PID_CONTROLLER_VERSION: 3,
    CONF_DYNAMIC_MINIMUM_SETPOINT_VERSION: 1,
}

# Overshoot protection
OVERSHOOT_PROTECTION_REQUIRED_DATASET = 40
OVERSHOOT_PROTECTION_SETPOINT = {
    HEATING_SYSTEM_HEAT_PUMP: 40,
    HEATING_SYSTEM_RADIATORS: 62,
    HEATING_SYSTEM_UNDERFLOOR: 45,
}

# Storage
STORAGE_OVERSHOOT_PROTECTION_VALUE = "overshoot_protection_value"

# Services
SERVICE_RESET_INTEGRAL = "reset_integral"
SERVICE_PULSE_WIDTH_MODULATION = "pulse_width_modulation"
SERVICE_SET_OVERSHOOT_PROTECTION_VALUE = "set_overshoot_protection_value"
SERVICE_START_OVERSHOOT_PROTECTION_CALCULATION = "start_overshoot_protection_calculation"

# Config steps
STEP_SETUP_GATEWAY = "gateway"
STEP_SETUP_SENSORS = "sensors"


# Enumerations
class FlameStatus(str, Enum):
    HEALTHY = "healthy"
    IDLE_OK = "idle_ok"
    STUCK_ON = "stuck_on"
    STUCK_OFF = "stuck_off"
    PWM_SHORT = "pwm_short"
    SHORT_CYCLING = "short_cycling"
    INSUFFICIENT_DATA = "insufficient_data"


class BoilerStatus(str, Enum):
    HOT_WATER = "hot_water"
    PREHEATING = "preheating"
    HEATING_UP = "heating_up"
    AT_SETPOINT = "at_setpoint"
    COOLING_DOWN = "cooling_down"
    ANTI_CYCLING = "anti_cycling"
    NEAR_SETPOINT = "near_setpoint"
    PUMP_STARTING = "pump_starting"
    WAITING_FOR_FLAME = "waiting_for_flame"
    OVERSHOOT_HANDLING = "overshoot_handling"
    OVERSHOOT_STABILIZED = "overshoot_stabilized"

    IDLE = "idle"
    INSUFFICIENT_DATA = "insufficient_data"


class PWMStatus(str, Enum):
    """The current state of Pulse Width Modulation"""
    ON = "on"
    OFF = "off"
    IDLE = "idle"

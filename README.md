# Danfoss Ally OTGW

A Home Assistant custom integration that controls a central heating boiler through an
[OpenTherm Gateway (OTGW)](https://otgw.tclcode.com/), driven by what your Danfoss Ally
radiator thermostats are asking for.

> **Unofficial.** This project is not made, endorsed or supported by Danfoss. "Danfoss" and
> "Ally" are only used to describe the thermostats this integration works with.

## Origin and license

This integration is a fork of [Smart Autotune Thermostat (SAT)](https://github.com/Alexwijn/SAT)
by [@Alexwijn](https://github.com/Alexwijn) and contributors. The boiler control (OpenTherm communication, heating curve,
PID control, pulse width modulation, overshoot protection calibration) comes from SAT.

Like SAT, it is licensed under the [GNU General Public License v3.0](LICENSE).

## How it works

The Danfoss Ally TRVs are the thermostats: you set the temperature of every room on its TRVs (or
with Home Assistant schedules). This integration only decides when the boiler fires, and how hot.

- **Setup** asks for the connection to the OpenTherm Gateway and the outside temperature, which
  sets the base flow temperature through the heating curve. A local outdoor sensor is best.
- **Without the outside temperature**, the boiler keeps heating on the last known value (6 hours
  by default), and then on an assumed one (5 °C by default). The TRVs keep every room at its target,
  so this only changes how warm the water is. The `outside_temperature_source` attribute shows
  which one is used. This can be turned off, then the boiler stays off until it is back.
- **The heating switch** (`switch.<name>`) turns the control of the boiler on and off. There is no
  target temperature of its own: the flow temperature follows the target of the TRV that is
  currently driving the boiler.
- **Frost guard**: when none of the TRVs is available, and it is below 3 °C outside, the boiler
  heats as if the house wants 10 °C. With a typical heating curve coefficient, this only makes the
  boiler fire when it really freezes; 15 °C fires it from around -2 °C outside. Both values are in
  the System Configuration options.

Entities that help to see what is going on:

| Entity | Shows |
|---|---|
| `switch.<name>` | On/off, and as attributes: the zones, `heat_demand`, `heat_allowed`, `frost_guard`, `failsafe`, PID and PWM state |
| `sensor.danfoss_ally_trvs_<name>` | Every Danfoss Ally TRV found, its area, its zone (or none), its status, temperatures and valve opening |
| `binary_sensor.heat_demand` | Whether any zone is calling for heat |
| `sensor.requested_setpoint_<name>` | The flow temperature that the heating curve and PID ask for |

TRVs that are not in any zone, and zones without any TRV, are logged as warnings.

## Changing the setup

**Reconfigure** (⋮ on the integration) changes one part of the setup, keeping the zones and
options:

- **Gateway connection**: e.g. a new socket address of a Nodo OTGW. A new address is tested
  before it is saved.
- **Outside temperature**.
- **Overshoot protection value**: calibrate again, or enter it. While calibrating, the integration
  lets go of the gateway, which accepts only one connection, and takes over again afterwards.

**Download diagnostics** (⋮ on the integration) gives a file with all settings, the zones, every
TRV and the current state of the boiler control. It contains no passwords or tokens.

## Zones

Rooms are set up as **zones**, based on your Home Assistant areas. Open the options of the
integration (**Configure**) and choose **Zones**. The screen shows every zone with its settings and
TRVs, and a dropdown to:

- **change one zone**: its settings only, then you are back at the overview;
- **add or remove zones**: pick the areas whose TRVs this integration controls. Only areas that
  contain a Danfoss Ally are listed. Existing zones keep their settings;
- **save and close**: nothing is saved before this.

Every zone either heats:

- **Independent**: the boiler fires as soon as one TRV in the zone asks for heat. Use this for
  rooms that must heat on their own, like a study or bedrooms.
- **Together**: the boiler waits until enough TRVs in the zone ask for heat (a minimum number of
  TRVs, or a minimum total valve opening from `pi_heating_demand`), so it can burn longer instead of
  short-cycling on one small radiator. Once the zone calls for heat, it keeps calling until all of
  its TRVs are satisfied. **Never wait longer than** makes sure a single TRV still gets heat after a
  while. New zones with more than one TRV start as Together.

The TRVs of a zone are found automatically: every Danfoss Ally (`014G2461`) device in that area.
Other climate devices in the same area, like air conditioning, are ignored. Adding, moving or
removing a TRV in Home Assistant is picked up right away, without reconfiguring.

The `zones` attribute of the heating switch shows every zone, its TRVs, which of them are heating,
their total valve opening and whether the zone is calling for heat.

## Settings

Changing any setting reloads the integration; no restart of Home Assistant is needed.

| Where | Settings |
|---|---|
| Configure → General | PID controller, maximum setpoint, heating curve coefficient, **Overshoot Hold** |
| Configure → Zones | The zones and their settings |
| Configure → System Configuration | Duty cycles, sensor age, **frost guard**, **outside temperature fallback** |
| Configure → Advanced Options | PWM, consumption, relative modulation, sample time |
| ⋮ → Reconfigure | Gateway connection, outside temperature, overshoot protection value |

## What it changes compared to SAT

- The TRVs are the thermostats: no inside sensor, presets, window sensors or target temperature of
  its own; the boiler is controlled by a switch, driven by zones of TRVs.
- A room only drives the boiler while its TRV reports that it is actually heating, and keeps
  requesting heat until the TRV itself is satisfied (configurable **Overshoot Hold**).
- The boiler never fires when no zone is calling for heat. A burn that is already running may
  finish, for at most 10 minutes.
- TRVs that stop reporting, or become unavailable, are left out instead of keeping the boiler
  burning. Without any available TRV, only the frost guard heats.
- Without the outside temperature, the boiler keeps heating on the last known and then an assumed
  value, instead of keeping its last setpoint.
- Switching the room that drives the boiler no longer causes a derivative spike in the PID.
- Changing settings keeps the connection to the gateway; a gateway that doesn't accept the
  connection is retried by Home Assistant instead of hanging.
- No error reporting to external services.

## Installation

1. In HACS, add this repository as a custom repository of type **Integration**.
2. Download **Danfoss Ally OTGW** and restart Home Assistant.
3. Add the integration under **Settings → Devices & services**: the gateway connection, the
   outside temperature and the overshoot protection value.
4. In **Configure → Zones**, add the areas with your Danfoss Ally TRVs.
5. Turn on the heating switch.

Do not run this integration and SAT against the same boiler at the same time: both would try
to control the OpenTherm Gateway.

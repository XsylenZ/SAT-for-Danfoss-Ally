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
  sets the base flow temperature through the heating curve. A local outdoor sensor is best; with
  only a weather service, the boiler stays off while that service is unavailable.
- **The heating switch** (`switch.<name>`) turns the control of the boiler on and off. There is no
  target temperature of its own: the flow temperature follows the target of the TRV that is
  currently driving the boiler.
- **Frost guard**: when none of the TRVs is available, and it is below 3 °C outside, the boiler
  heats as if the house wants 10 °C. With a typical heating curve coefficient, this only makes the
  boiler fire when it really freezes.

Entities that help to see what is going on:

| Entity | Shows |
|---|---|
| `switch.<name>` | On/off, and as attributes: the zones, `heat_demand`, `heat_allowed`, `frost_guard`, `failsafe`, PID and PWM state |
| `sensor.danfoss_ally_trvs_<name>` | Every Danfoss Ally TRV found, its area, its zone (or none), its status, temperatures and valve opening |
| `binary_sensor.heat_demand` | Whether any zone is calling for heat |
| `sensor.requested_setpoint_<name>` | The flow temperature that the heating curve and PID ask for |

TRVs that are not in any zone, and zones without any TRV, are logged as warnings.

## Zones

Rooms are set up as **zones**, based on your Home Assistant areas. After adding the integration,
open its options and choose **Zones**:

1. Pick the areas whose TRVs this integration should control. Only areas that contain a Danfoss
   Ally are listed.
2. Configure each zone:
   - **Independent**: the boiler fires as soon as one TRV in the zone asks for heat. Use this for
     rooms that must heat on their own, like a study or bedrooms.
   - **Together**: the boiler waits until enough TRVs in the zone ask for heat (a minimum number
     of TRVs, or a minimum total valve opening from `pi_heating_demand`), so it can burn longer
     instead of short-cycling on one small radiator. Once the zone calls for heat, it keeps calling
     until all of its TRVs are satisfied. **Never wait longer than** makes sure a single TRV still
     gets heat after a while.

The TRVs of a zone are found automatically: every Danfoss Ally (`014G2461`) device in that area.
Other climate devices in the same area, like air conditioning, are ignored. Adding, moving or
removing a TRV in Home Assistant is picked up right away, without reconfiguring.

The `zones` attribute of the climate entity shows every zone, its TRVs, which of them are heating,
their total valve opening and whether the zone is calling for heat.

## What it changes compared to SAT

- A room only drives the boiler while its TRV reports that it is actually heating, and keeps
  requesting heat until the TRV itself is satisfied (configurable **Overshoot Hold**).
- The boiler never fires when no room is calling for heat. A burn that is already running may
  finish, for at most 10 minutes.
- TRVs that stop reporting, or become unavailable, are left out instead of keeping the boiler
  burning. Without the temperatures needed to control the boiler, the heater is turned off.
- Switching the room that drives the boiler no longer causes a derivative spike in the PID.
- No error reporting to external services.

## Installation

1. In HACS, add this repository as a custom repository of type **Integration**.
2. Download **Danfoss Ally OTGW** and restart Home Assistant.
3. Add the integration under **Settings → Devices & services**.

Do not run this integration and SAT against the same boiler at the same time: both would try
to control the OpenTherm Gateway.

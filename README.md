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

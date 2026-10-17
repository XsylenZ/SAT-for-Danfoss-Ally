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

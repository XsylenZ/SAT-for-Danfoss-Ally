import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any, TYPE_CHECKING

from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry

from .const import *
from .helpers import float_value

if TYPE_CHECKING:
    from .area import Area

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Trv:
    """A Danfoss Ally TRV, with the entities of its device that we use."""
    climate: str
    pi_heating_demand: str | None = None
    load_estimate: str | None = None
    load_room_mean: str | None = None
    area_id: str | None = None


def is_ally(device: device_registry.DeviceEntry) -> bool:
    """Return True when the device is a Danfoss Ally TRV."""
    if device.manufacturer != ALLY_MANUFACTURER:
        return False

    return device.model_id in ALLY_MODEL_IDS or device.model == ALLY_MODEL


def discover_trvs(hass: HomeAssistant, area_id: str | None = None) -> list[Trv]:
    """Find the Danfoss Ally TRVs in the given area, or in all areas when none is given."""
    entities = entity_registry.async_get(hass)
    devices = device_registry.async_get(hass)

    trvs = []
    for device in devices.devices.values():
        if device.disabled or not is_ally(device):
            continue

        device_entities = [entry for entry in entity_registry.async_entries_for_device(entities, device.id) if not entry.disabled]
        if (climate := next((entry for entry in device_entities if entry.domain == CLIMATE_DOMAIN), None)) is None:
            continue

        # An area assigned to the entity itself takes precedence over the area of its device
        trv_area_id = climate.area_id or device.area_id
        if area_id is not None and trv_area_id != area_id:
            continue

        trvs.append(Trv(
            climate=climate.entity_id,
            pi_heating_demand=_find_entity(device_entities, SENSOR_DOMAIN, "pi_heating_demand"),
            load_estimate=_find_entity(device_entities, SENSOR_DOMAIN, "load_estimate"),
            load_room_mean=_find_entity(device_entities, NUMBER_DOMAIN, "load_room_mean"),
            area_id=trv_area_id,
        ))

    return sorted(trvs, key=lambda trv: trv.climate)


def _find_entity(entries: list[entity_registry.RegistryEntry], domain: str, key: str) -> str | None:
    """Find the entity of a device that exposes the given Zigbee2MQTT property."""
    for entry in entries:
        if entry.domain != domain:
            continue

        if entry.entity_id.endswith(f"_{key}") or entry.unique_id.endswith(f"_{key}") or f"_{key}_" in entry.unique_id:
            return entry.entity_id

    return None


class Zone:
    """A Home Assistant area, whose Danfoss Ally TRVs call for heat as one."""

    def __init__(self, settings: dict[str, Any]):
        settings = {**ZONE_DEFAULTS, **settings}

        self.area_id: str = settings[CONF_ZONE_AREA]
        self.mode: str = settings[CONF_ZONE_MODE]
        self.minimum_open: int = int(settings[CONF_ZONE_MINIMUM_OPEN])
        self.minimum_demand: float = float(settings[CONF_ZONE_MINIMUM_DEMAND])
        self.maximum_wait: float = float(settings[CONF_ZONE_MAXIMUM_WAIT]) * 60

        self.trvs: list[Trv] = []
        self.calling: bool = False
        self.available: bool = False
        self.heating: list[str] = []
        self.opening: float = 0.0

        self._waiting_since: float | None = None

    def update(self, areas: dict[str, "Area"], hass: HomeAssistant | None) -> None:
        """Determine whether this zone is calling for heat."""
        available = [areas[trv.climate] for trv in self.trvs if areas[trv.climate].available]
        heating = [trv for trv in self.trvs if areas[trv.climate] in available and (error := areas[trv.climate].error) is not None and error.value > 0]

        self.available = len(available) > 0
        self.heating = [trv.climate for trv in heating]
        self.opening = sum(opening(hass, trv) for trv in heating)

        # Nobody in this zone wants heat (anymore)
        if not heating:
            self.calling = False
            self._waiting_since = None
            return

        # Once calling, the zone keeps calling until all of its TRVs are satisfied, so it doesn't flap
        if self.mode == ZONE_MODE_INDEPENDENT or self.calling:
            self.calling = True
            return

        if len(heating) >= self.minimum_open or (self.minimum_demand > 0 and self.opening >= self.minimum_demand):
            _LOGGER.info("Zone %s is calling for heat (%d TRVs heating, %.0f%% open)", self.area_id, len(heating), self.opening)
            self.calling = True
            return

        # Don't let a single TRV wait forever for the others
        if self._waiting_since is None:
            self._waiting_since = monotonic()

        if monotonic() - self._waiting_since >= self.maximum_wait:
            _LOGGER.info("Zone %s waited %.0f minutes for more TRVs, calling for heat", self.area_id, self.maximum_wait / 60)
            self.calling = True

    def summary(self, hass: HomeAssistant | None) -> dict[str, Any]:
        """Return the state of this zone, for the attributes of the climate entity."""
        area = area_registry.async_get(hass).async_get_area(self.area_id) if hass is not None else None

        return {
            "name": area.name if area is not None else self.area_id,
            "mode": self.mode,
            "calling": self.calling,
            "trvs": [trv.climate for trv in self.trvs],
            "heating": self.heating,
            "opening": round(self.opening, 1),
        }


def opening(hass: HomeAssistant | None, trv: Trv) -> float:
    """Return how far the valve of the TRV is open, in percent."""
    if hass is None or trv.pi_heating_demand is None:
        return 0.0

    if (state := hass.states.get(trv.pi_heating_demand)) is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return 0.0

    return float_value(state.state) or 0.0

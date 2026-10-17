"""Helpers to register Danfoss Ally TRVs with Home Assistant for the tests."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry, device_registry, entity_registry
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def add_trv(
        hass: HomeAssistant,
        area_name: str,
        object_id: str,
        manufacturer: str = "Danfoss",
        model: str = "Ally thermostat",
        model_id: str | None = "014G2461",
) -> str:
    """Register a TRV like Zigbee2MQTT does: a device in an area, with a climate and its other entities."""
    areas = area_registry.async_get(hass)
    area = areas.async_get_area_by_name(area_name) or areas.async_create(area_name)

    config_entry = MockConfigEntry(domain="mqtt")
    config_entry.add_to_hass(hass)

    devices = device_registry.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={("mqtt", object_id)},
        manufacturer=manufacturer,
        model=model,
        model_id=model_id,
    )
    devices.async_update_device(device.id, area_id=area.id)

    entities = entity_registry.async_get(hass)
    for domain, key in (("climate", None), ("sensor", "pi_heating_demand"), ("sensor", "load_estimate"), ("number", "load_room_mean")):
        unique_id = f"0x{object_id}_{key or domain}_zigbee2mqtt"
        suggested_object_id = f"{object_id}_{key}" if key else object_id
        entities.async_get_or_create(domain, "mqtt", unique_id, suggested_object_id=suggested_object_id, device_id=device.id, config_entry=config_entry)

    await hass.async_block_till_done()
    return f"climate.{object_id}"


async def move_trv(hass: HomeAssistant, object_id: str, area_name: str) -> None:
    """Move the device of a TRV to another area."""
    areas = area_registry.async_get(hass)
    area = areas.async_get_area_by_name(area_name) or areas.async_create(area_name)

    devices = device_registry.async_get(hass)
    device = devices.async_get_device(identifiers={("mqtt", object_id)})
    devices.async_update_device(device.id, area_id=area.id)

    await hass.async_block_till_done()

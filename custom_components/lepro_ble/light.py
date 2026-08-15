"""Light platform for Lepro BLE bulbs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LeproConfigEntry
from .const import DOMAIN, MAX_KELVIN, MIN_KELVIN
from .device import LeproBulb
from .protocol import (
    BRIGHTNESS_MAX,
    BRIGHTNESS_MIN,
    HUE_MAX,
    SAT_MAX,
    TEMP_MAX,
    TEMP_MIN,
    VAL_MAX,
    decode_hsv,
    encode_hsv,
)

MODE_WHITE = 0
MODE_COLOR = 1


def _scale(value: float, src: tuple[float, float], dst: tuple[float, float]) -> int:
    ratio = (value - src[0]) / (src[1] - src[0])
    return round(min(max(dst[0] + ratio * (dst[1] - dst[0]), dst[0]), dst[1]))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LeproConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([LeproLight(entry.runtime_data, entry.unique_id, entry.title)])


class LeproLight(LightEntity):
    """A Lepro bulb over BLE."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = {ColorMode.HS, ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(self, bulb: LeproBulb, address: str, title: str) -> None:
        self._bulb = bulb
        self._attr_unique_id = address
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, bulb.mac)},
            manufacturer="Lepro",
            name=title,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._bulb.register_callback(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._bulb.available

    @property
    def is_on(self) -> bool | None:
        if (value := self._bulb.state.get("d1")) is None:
            return None
        return bool(value)

    @property
    def color_mode(self) -> ColorMode:
        if self._bulb.state.get("d2") == MODE_COLOR:
            return ColorMode.HS
        return ColorMode.COLOR_TEMP

    @property
    def brightness(self) -> int | None:
        """d3 drives white mode; in colour mode brightness is d5's value field."""
        if self.color_mode is ColorMode.HS:
            if (raw := self._bulb.state.get("d5")) is None:
                return None
            _, _, value = decode_hsv(raw)
            return _scale(value, (0, VAL_MAX), (0, 255))
        if (raw := self._bulb.state.get("d3")) is None:
            return None
        return _scale(raw, (BRIGHTNESS_MIN, BRIGHTNESS_MAX), (1, 255))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        if (raw := self._bulb.state.get("d5")) is None:
            return None
        hue, sat, _ = decode_hsv(raw)
        return (min(hue, HUE_MAX), sat / SAT_MAX * 100)

    @property
    def color_temp_kelvin(self) -> int | None:
        if (raw := self._bulb.state.get("d4")) is None:
            return None
        return _scale(raw, (TEMP_MIN, TEMP_MAX), (MIN_KELVIN, MAX_KELVIN))

    async def async_turn_on(self, **kwargs: Any) -> None:
        dps: dict[str, Any] = {"d1": 1}

        if ATTR_HS_COLOR in kwargs:
            hue, sat = kwargs[ATTR_HS_COLOR]
            brightness = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
            dps["d2"] = MODE_COLOR
            dps["d5"] = encode_hsv(
                round(hue),
                _scale(sat, (0, 100), (0, SAT_MAX)),
                _scale(brightness, (0, 255), (0, VAL_MAX)),
            )
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            dps["d2"] = MODE_WHITE
            dps["d4"] = _scale(
                kwargs[ATTR_COLOR_TEMP_KELVIN],
                (MIN_KELVIN, MAX_KELVIN),
                (TEMP_MIN, TEMP_MAX),
            )

        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            if dps.get("d2", self._bulb.state.get("d2")) == MODE_COLOR:
                hue, sat = self.hs_color or (0, 0)
                dps["d5"] = encode_hsv(
                    round(hue),
                    _scale(sat, (0, 100), (0, SAT_MAX)),
                    _scale(brightness, (0, 255), (0, VAL_MAX)),
                )
            else:
                dps["d3"] = _scale(
                    brightness, (1, 255), (BRIGHTNESS_MIN, BRIGHTNESS_MAX)
                )

        await self._bulb.async_set_dp(**dps)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bulb.async_set_dp(d1=0)

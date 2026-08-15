"""Constants for the Lepro BLE integration."""

DOMAIN = "lepro_ble"

CONF_MAC = "mac"

# le_strip_t2color in libiot-core.so computes kelvin = d4 * 3800 / 1000 + 2700,
# so d4 0..1000 is exactly linear over 2700..6500 K. Matches the B1 packaging.
MIN_KELVIN = 2700
MAX_KELVIN = 6500

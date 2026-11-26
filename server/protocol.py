"""The data contract shared by the ESP32 firmware, the server, the device
simulator and the Android app. Keep firmware/aquasense_esp32_v2 and
mobile/src/lib/protocol.ts in step with this file.

Real sensors on the ESP32 (firmware v1 pins):
  flow_rate_Lmin, total_volume_L   ZJ-S201 flow sensor   GPIO35
  pump_current_A, current_ADC      ACS712                GPIO33
  turbidity_ADC, turbidity_voltage_V  turbidity sensor   GPIO32

Channels the ESP32 fabricates until the sensors are fitted ("virtual
sensors"): tds_in_ppm, tds_out_ppm, pressure_bar, water_temp_C.

Fault overlay: set per device from the company dashboard, returned to the
ESP32 in every /sensor-data response, and applied on the device to both real
and virtual channels, so the fault shows on Wi-Fi AND over Bluetooth exactly
as if it had happened. Levels 0-3; severity s = level / 3.

  clog        filter/line clogging   flow x(1-0.7s)  pressure x(1+0.45s)  current x(1+0.15s)
  pump        pump weakness          flow x(1-0.45s) pressure x(1-0.55s)  current x(1-0.35s)
  membrane    RO membrane wear       rejection falls by 0.45s (TDS out rises)
  turbidity   pre-filter exhausted   turbidity voltage x(1-0.35s) (water gets cloudy)
  sensor      one sensor misbehaves: none | flow_stuck | tds_out_dead | turbidity_disconnected | pressure_frozen
  tds_source  source water TDS (ppm): 300 municipal, 800 mixed, 1500 borewell (not a fault)
"""

from __future__ import annotations

FAULTS = ("clog", "pump", "membrane", "turbidity")
SENSOR_MODES = ("none", "flow_stuck", "tds_out_dead", "turbidity_disconnected", "pressure_frozen")
TDS_SOURCES = (300, 800, 1500)

# Healthy reference values for the virtual channels (the real ones are learnt
# per device as a baseline).
VIRTUAL_BASE = {"pressure_bar": 5.5, "rejection": 0.955, "water_temp_C": 26.0}

READING_FIELDS = [
    "flow_rate_Lmin", "total_volume_L", "pump_current_A", "current_ADC", "turbidity_ADC", "turbidity_voltage_V",
    "tds_in_ppm", "tds_out_ppm", "pressure_bar", "water_temp_C",
]

# Bluetooth LE GATT (ESP32 = peripheral, phone = central)
BLE_NAME_PREFIX = "AquaSense-"
BLE_SERVICE_UUID = "8f2a0001-5a1c-4c2e-9b6e-61717561736e"
BLE_DATA_UUID = "8f2a0002-5a1c-4c2e-9b6e-61717561736e"   # notify, 20-byte packet below
BLE_INFO_UUID = "8f2a0003-5a1c-4c2e-9b6e-61717561736e"   # read, "AQ-A1B2C3;fw=2.0.0"

# 20-byte little-endian packet, one notification every 2 s:
#   0 u8  version (2)        1 u8  sequence
#   2 u16 flow  mL/min       4 i16 pump current mA
#   6 u16 turbidity mV       8 u16 TDS in ppm
#  10 u16 TDS out ppm x10   12 u16 pressure mbar
#  14 i16 temperature x100  16 u16 total volume L x10 (wraps)
#  18 u8  flags: bit0 running, bit1 Wi-Fi up, bit2 server reachable
#  19 u8  XOR of bytes 0..18
BLE_PACKET_FORMAT = "<BBHhHHHHhHBB"


def default_overlay() -> dict:
    return {"rev": 0, "clog": 0, "pump": 0, "membrane": 0, "turbidity": 0, "sensor": "none", "tds_source": 300}


def validate_overlay_patch(patch: dict) -> dict:
    out = {}
    for k in FAULTS:
        if k in patch:
            v = int(patch[k])
            if not 0 <= v <= 3:
                raise ValueError(f"{k} must be 0-3")
            out[k] = v
    if "sensor" in patch:
        if patch["sensor"] not in SENSOR_MODES:
            raise ValueError(f"sensor must be one of {SENSOR_MODES}")
        out["sensor"] = patch["sensor"]
    if "tds_source" in patch:
        v = int(patch["tds_source"])
        if not 50 <= v <= 3000:
            raise ValueError("tds_source must be 50-3000 ppm")
        out["tds_source"] = v
    return out


def ble_packet_pack(r: dict, seq: int, flags: int) -> bytes:
    import struct

    def u16(x):
        return max(0, min(65535, int(round(x))))

    def i16(x):
        return max(-32768, min(32767, int(round(x))))

    body = struct.pack(
        BLE_PACKET_FORMAT[:-1],
        2, seq & 0xFF,
        u16(r["flow_rate_Lmin"] * 1000), i16(r["pump_current_A"] * 1000), u16(r["turbidity_voltage_V"] * 1000),
        u16(r["tds_in_ppm"]), u16(r["tds_out_ppm"] * 10), u16(r["pressure_bar"] * 1000), i16(r["water_temp_C"] * 100),
        u16((r.get("total_volume_L", 0) * 10) % 65536), flags & 0xFF,
    )
    x = 0
    for b in body:
        x ^= b
    return body + bytes([x])


def ble_packet_unpack(data: bytes) -> dict:
    import struct

    if len(data) != 20:
        raise ValueError("packet must be 20 bytes")
    x = 0
    for b in data[:19]:
        x ^= b
    if x != data[19]:
        raise ValueError("checksum mismatch")
    v = struct.unpack(BLE_PACKET_FORMAT, data)
    return {
        "version": v[0], "seq": v[1], "flow_rate_Lmin": v[2] / 1000, "pump_current_A": v[3] / 1000,
        "turbidity_voltage_V": v[4] / 1000, "tds_in_ppm": float(v[5]), "tds_out_ppm": v[6] / 10,
        "pressure_bar": v[7] / 1000, "water_temp_C": v[8] / 100, "total_volume_L": v[9] / 10,
        "running": bool(v[10] & 1), "wifi": bool(v[10] & 2), "server_ok": bool(v[10] & 4),
    }

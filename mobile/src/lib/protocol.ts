// Mirror of server/protocol.py: keep the two in step.
import { toByteArray } from 'base64-js';

export const BLE_NAME_PREFIX = 'AquaSense-';
export const BLE_SERVICE_UUID = '8f2a0001-5a1c-4c2e-9b6e-61717561736e';
export const BLE_DATA_UUID = '8f2a0002-5a1c-4c2e-9b6e-61717561736e';
export const BLE_INFO_UUID = '8f2a0003-5a1c-4c2e-9b6e-61717561736e';

export interface Reading {
  ts: number; // seconds
  flow_rate_Lmin: number;
  pump_current_A: number;
  turbidity_voltage_V: number;
  tds_in_ppm: number;
  tds_out_ppm: number;
  pressure_bar: number;
  water_temp_C: number;
  total_volume_L: number;
  running?: boolean;
  wifi?: boolean;
  server_ok?: boolean;
  seq?: number;
}

/** AquaSense-A1B2C3 <-> AQ-A1B2C3 */
export const deviceIdFromBleName = (name?: string | null) =>
  name && name.startsWith(BLE_NAME_PREFIX) ? `AQ-${name.slice(BLE_NAME_PREFIX.length)}` : null;

/** Decode the 20-byte notification (base64 from react-native-ble-plx). */
export function decodePacket(b64: string): Reading | null {
  const b = toByteArray(b64);
  if (b.length !== 20) return null;
  let x = 0;
  for (let i = 0; i < 19; i++) x ^= b[i];
  if (x !== b[19]) return null;
  const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const flags = v.getUint8(18);
  return {
    ts: Date.now() / 1000,
    seq: v.getUint8(1),
    flow_rate_Lmin: v.getUint16(2, true) / 1000,
    pump_current_A: v.getInt16(4, true) / 1000,
    turbidity_voltage_V: v.getUint16(6, true) / 1000,
    tds_in_ppm: v.getUint16(8, true),
    tds_out_ppm: v.getUint16(10, true) / 10,
    pressure_bar: v.getUint16(12, true) / 1000,
    water_temp_C: v.getInt16(14, true) / 100,
    total_volume_L: v.getUint16(16, true) / 10,
    running: (flags & 1) !== 0,
    wifi: (flags & 2) !== 0,
    server_ok: (flags & 4) !== 0,
  };
}

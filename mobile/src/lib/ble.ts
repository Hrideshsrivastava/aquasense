// Bluetooth LE link to the purifier's ESP32 (adapted from MuscleSense's useBLE).
// The ESP32 is the peripheral ("AquaSense-A1B2C3"); the phone scans, connects
// and subscribes to a 20-byte notification every 2 s.
import { useCallback, useEffect, useRef, useState } from 'react';
import { PermissionsAndroid, Platform } from 'react-native';
import type { BleManager, Device, Subscription } from 'react-native-ble-plx';
import { fromByteArray, toByteArray } from 'base64-js';

import { BLE_DATA_UUID, BLE_INFO_UUID, BLE_NAME_PREFIX, BLE_SERVICE_UUID, decodePacket, deviceIdFromBleName, type Reading } from './protocol';

export const bleSupported = Platform.OS === 'android' || Platform.OS === 'ios';

let manager: BleManager | null = null;
function getManager(): BleManager {
  // Created lazily: the native module does not exist on web.
  if (!manager) {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { BleManager: M } = require('react-native-ble-plx') as typeof import('react-native-ble-plx');
    manager = new M();
  }
  return manager;
}

async function requestPermissions(): Promise<boolean> {
  if (Platform.OS !== 'android') return true;
  if ((Platform.Version as number) >= 31) {
    const r = await PermissionsAndroid.requestMultiple([
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN,
      PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT,
    ]);
    return Object.values(r).every((v) => v === PermissionsAndroid.RESULTS.GRANTED);
  }
  const r = await PermissionsAndroid.request(PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION);
  return r === PermissionsAndroid.RESULTS.GRANTED;
}

export interface FoundDevice { id: string; name: string; deviceId: string | null; rssi: number | null }

export function useAquaBLE() {
  const [scanning, setScanning] = useState(false);
  const [found, setFound] = useState<FoundDevice[]>([]);
  const [connected, setConnected] = useState<FoundDevice | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [readings, setReadings] = useState<Reading[]>([]);
  const [error, setError] = useState<string | null>(null);
  const sub = useRef<Subscription | null>(null);
  const dev = useRef<Device | null>(null);
  const scanTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopScan = useCallback(() => {
    if (!bleSupported) return;
    getManager().stopDeviceScan();
    if (scanTimer.current) clearTimeout(scanTimer.current);
    setScanning(false);
  }, []);

  const scan = useCallback(async () => {
    if (!bleSupported) { setError('Bluetooth needs the AquaSense Android app.'); return; }
    if (!(await requestPermissions())) { setError('Allow Bluetooth permission to connect to the purifier.'); return; }
    setError(null);
    setFound([]);
    setScanning(true);
    getManager().startDeviceScan(null, { allowDuplicates: false }, (err, d) => {
      if (err) { setError(err.message); setScanning(false); return; }
      const name = d?.name || d?.localName || '';
      const advertisesService = d?.serviceUUIDs?.some((u) => u.toLowerCase() === BLE_SERVICE_UUID);
      if (!d || (!name.startsWith(BLE_NAME_PREFIX) && !advertisesService)) return;
      setFound((prev) => (prev.some((p) => p.id === d.id) ? prev : [...prev, { id: d.id, name: name || d.id, deviceId: deviceIdFromBleName(name), rssi: d.rssi }]));
    });
    scanTimer.current = setTimeout(stopScan, 12000);
  }, [stopScan]);

  const cleanup = useCallback(() => {
    sub.current?.remove();
    sub.current = null;
    dev.current = null;
    setConnected(null);
  }, []);

  const connect = useCallback(async (f: FoundDevice) => {
    stopScan();
    setError(null);
    setReadings([]);
    try {
      const d = await getManager().connectToDevice(f.id, { timeout: 10000 });
      await d.discoverAllServicesAndCharacteristics();
      dev.current = d;
      try {
        const c = await d.readCharacteristicForService(BLE_SERVICE_UUID, BLE_INFO_UUID);
        if (c.value) setInfo(String.fromCharCode(...toByteArray(c.value)));
      } catch { /* info is optional */ }
      setConnected(f);
      d.onDisconnected(() => cleanup());
      sub.current = d.monitorCharacteristicForService(BLE_SERVICE_UUID, BLE_DATA_UUID, (err, ch) => {
        if (err) { setError(err.message); cleanup(); return; }
        const r = ch?.value ? decodePacket(ch.value) : null;
        if (r) setReadings((prev) => [...prev.slice(-179), r]);
      });
    } catch (e) {
      setError(`Could not connect: ${e instanceof Error ? e.message : String(e)}`);
      cleanup();
    }
  }, [cleanup, stopScan]);

  const disconnect = useCallback(async () => {
    const d = dev.current;
    cleanup();
    if (d) { try { await getManager().cancelDeviceConnection(d.id); } catch { /* already gone */ } }
  }, [cleanup]);

  useEffect(() => () => { stopScan(); sub.current?.remove(); }, [stopScan]);

  return { supported: bleSupported, scanning, found, connected, info, readings, error, scan, stopScan, connect, disconnect, setReadings };
}

// For tests: build a packet like the ESP32 does (inverse of decodePacket).
export function encodePacketForTest(r: Reading, seq = 0, flags = 7): string {
  const b = new Uint8Array(20);
  const v = new DataView(b.buffer);
  v.setUint8(0, 2); v.setUint8(1, seq & 255);
  v.setUint16(2, Math.round(r.flow_rate_Lmin * 1000), true);
  v.setInt16(4, Math.round(r.pump_current_A * 1000), true);
  v.setUint16(6, Math.round(r.turbidity_voltage_V * 1000), true);
  v.setUint16(8, Math.round(r.tds_in_ppm), true);
  v.setUint16(10, Math.round(r.tds_out_ppm * 10), true);
  v.setUint16(12, Math.round(r.pressure_bar * 1000), true);
  v.setInt16(14, Math.round(r.water_temp_C * 100), true);
  v.setUint16(16, Math.round(r.total_volume_L * 10) % 65536, true);
  v.setUint8(18, flags);
  let x = 0;
  for (let i = 0; i < 19; i++) x ^= b[i];
  b[19] = x;
  return fromByteArray(b);
}

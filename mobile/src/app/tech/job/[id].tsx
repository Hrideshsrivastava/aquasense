import { useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Linking, Platform, Pressable, Text, View } from 'react-native';

import { Body, Button, Card, HealthRow, Muted, Pill, Row, Screen, Stat, Title } from '@/components/ui';
import { useAquaBLE } from '@/lib/ble';
import { diagnose, MIN_READINGS, toReport, type Diagnosis } from '@/lib/diagnosis';
import type { Reading } from '@/lib/protocol';
import { useSession } from '@/lib/session';
import { usePalette } from '@/lib/theme';
import { COMPONENT_LABEL, JOB_LABEL, type ComponentKey, type Job } from '@/lib/types';

const VERDICT = {
  confirmed: { text: 'Confirmed', color: '#d03b3b' },
  different: { text: 'Different fault found', color: '#ec835a' },
  no_fault: { text: 'No fault found', color: '#0ca30c' },
};

export default function JobScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const jid = Number(id);
  const c = usePalette();
  const { jobs, devices, readings: live, api, submitReport } = useSession();
  const job: Job | undefined = jobs[jid];
  const expected = job?.device_id ?? null;
  const ble = useAquaBLE();
  const relay = !ble.supported; // web build: use the server's live data in place of Bluetooth
  const relayStart = useRef(Date.now() / 1000);
  const [result, setResult] = useState<Diagnosis | null>(null);
  const [sent, setSent] = useState<'sent' | 'queued' | null>(null);
  const [busy, setBusy] = useState(false);

  const stream: Reading[] = useMemo(() => {
    if (!relay) return ble.readings;
    return (expected ? live[expected] || [] : []).filter((r) => r.ts >= relayStart.current);
  }, [relay, ble.readings, live, expected]);
  const last = stream[stream.length - 1];
  const linkedId = relay ? expected : ble.info?.split(';')[0] ?? ble.connected?.deviceId ?? null;
  const wrongDevice = !!(expected && linkedId && linkedId !== expected);

  useEffect(() => { if (relay) relayStart.current = Date.now() / 1000; }, [relay]);

  const setStatus = async (status: string) => {
    setBusy(true);
    try { await api(`/api/jobs/${jid}`, 'PATCH', { status }); } catch (e) { Alert.alert('Could not update', String(e)); } finally { setBusy(false); }
  };
  const run = () => {
    const baseline = job?.device?.baseline ?? devices[expected ?? '']?.baseline ?? null;
    setResult(diagnose(stream, baseline));
    setSent(null);
  };
  const submit = async () => {
    if (!result || !job) return;
    setBusy(true);
    const r = await submitReport(job.id, toReport(result, job.hypothesis, relay ? 'server-relay' : 'bluetooth'));
    setSent(r);
    setBusy(false);
  };

  const hyp = expected ? devices[expected]?.hypothesis : null;
  return (
    <Screen title={job ? `Job #${job.id}` : 'Purifier check'} back>
      {job ? (
        <Card>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
            <Title>{job.hypothesis_label}</Title>
            <Pill label={JOB_LABEL[job.status]} color="#2a78d6" />
          </View>
          <Body>{job.device?.name} · {job.device_id}</Body>
          <Muted>{job.device?.address}</Muted>
          {job.device?.customer ? (
            <Row style={{ marginTop: 8 }}>
              <Body style={{ flex: 1 }}>{job.device.customer.name}</Body>
              {Platform.OS !== 'web' ? (
                <Pressable accessibilityRole="button" onPress={() => Linking.openURL(`tel:${job.device!.customer!.phone.replace(/\s/g, '')}`)}>
                  <Text style={{ color: c.accent, fontWeight: '600' }}>Call {job.device.customer.phone}</Text>
                </Pressable>
              ) : <Muted>{job.device.customer.phone}</Muted>}
            </Row>
          ) : null}
          {job.note ? <Muted style={{ marginTop: 6 }}>“{job.note}”</Muted> : null}
          {hyp && hyp.evidence?.length ? <Muted style={{ marginTop: 6 }}>Server quick check: {hyp.evidence.join(' · ')}</Muted> : null}
          {job.estimate ? <Muted style={{ marginTop: 6 }}>Carry {job.estimate.part} · estimate ₹{job.estimate.total}</Muted> : null}
          <Row style={{ marginTop: 12 }}>
            {job.status === 'assigned' || job.status === 'new' ? <View style={{ flex: 1 }}><Button label="On my way" onPress={() => setStatus('en_route')} busy={busy} /></View> : null}
            {job.status === 'en_route' ? <View style={{ flex: 1 }}><Button label="I have arrived" onPress={() => setStatus('on_site')} busy={busy} /></View> : null}
          </Row>
        </Card>
      ) : (
        <Card><Body>Connect to any AquaSense purifier nearby and run the full check. Results are shown here; they are uploaded only for a job.</Body></Card>
      )}

      <Card>
        <Title>On-site check</Title>
        {relay ? (
          <Muted>Bluetooth is not available in this browser build. Using the purifier's live readings from the server instead.</Muted>
        ) : ble.connected ? (
          <Row>
            <Body style={{ flex: 1 }}>🔵 Connected to {ble.connected.name}</Body>
            <Pressable onPress={ble.disconnect}><Text style={{ color: c.accent, fontWeight: '600' }}>Disconnect</Text></Pressable>
          </Row>
        ) : (
          <>
            <Muted>Stand near the purifier. It advertises as “AquaSense-{expected ? expected.slice(3) : '…'}”.</Muted>
            <View style={{ marginTop: 10 }}>
              <Button label={ble.scanning ? 'Scanning…' : 'Find purifier'} onPress={ble.scan} disabled={ble.scanning} />
            </View>
            {ble.found.map((f) => (
              <Pressable key={f.id} onPress={() => ble.connect(f)} style={{ paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: c.border }}>
                <Body style={{ fontWeight: f.deviceId === expected ? '700' : '400' }}>{f.name}{f.deviceId === expected ? '  ← this job' : ''}</Body>
                <Muted>{f.deviceId ?? f.id}{f.rssi ? ` · signal ${f.rssi} dBm` : ''}</Muted>
              </Pressable>
            ))}
            {ble.error ? <Body style={{ color: '#d03b3b', marginTop: 8 }}>{ble.error}</Body> : null}
          </>
        )}
        {wrongDevice ? <Body style={{ color: '#d03b3b', marginTop: 8 }}>This is {linkedId}, not {expected}. Check you are at the right purifier.</Body> : null}

        {(ble.connected || relay) ? (
          <>
            <Row style={{ marginTop: 12 }}>
              <Stat label="flow L/min" value={last ? last.flow_rate_Lmin.toFixed(2) : '–'} />
              <Stat label="pump A" value={last ? last.pump_current_A.toFixed(2) : '–'} />
              <Stat label="pressure bar" value={last ? last.pressure_bar.toFixed(2) : '–'} />
            </Row>
            <Row style={{ marginTop: 8 }}>
              <Stat label="TDS in" value={last ? last.tds_in_ppm.toFixed(0) : '–'} />
              <Stat label="TDS out" value={last ? last.tds_out_ppm.toFixed(1) : '–'} />
              <Stat label="turbidity V" value={last ? last.turbidity_voltage_V.toFixed(2) : '–'} />
            </Row>
            <LiveDebug last={last} stats={relay ? null : ble.stats} info={ble.info} />
            <View style={{ height: 8, backgroundColor: c.track, borderRadius: 4, marginTop: 12, overflow: 'hidden' }}>
              <View style={{ height: '100%', width: `${Math.min(100, (stream.length / MIN_READINGS) * 100)}%`, backgroundColor: c.accent }} />
            </View>
            <Muted style={{ marginTop: 4 }}>{stream.length} readings collected{stream.length < MIN_READINGS ? ` · ${MIN_READINGS - stream.length} more for a full check` : ''}</Muted>
            <View style={{ marginTop: 12 }}>
              <Button label={result ? 'Run the full check again' : 'Run full diagnosis'} onPress={run} disabled={stream.length < MIN_READINGS} />
            </View>
          </>
        ) : null}
      </Card>

      {result ? (
        <>
          <Card>
            {job ? (() => {
              const v = toReport(result, job.hypothesis, 'bluetooth').verdict;
              return <View style={{ marginBottom: 8 }}><Pill label={`${VERDICT[v].text}${v === 'confirmed' ? ` · ${job.hypothesis_label}` : ''}`} color={VERDICT[v].color} /></View>;
            })() : null}
            <Title>{result.label}</Title>
            <Muted>Confidence {Math.round(result.confidence * 100)}% · {result.n} readings</Muted>
            <Body style={{ marginTop: 8 }}>{result.reason}</Body>
            {result.evidence.map((e) => <Muted key={e}>• {e}</Muted>)}
          </Card>
          <Card>
            <Title>Component health</Title>
            {(Object.keys(result.health) as ComponentKey[]).map((k) => (
              <HealthRow key={k} name={COMPONENT_LABEL[k]} value={result.health[k]}
                sub={result.rul[k] ? (result.rul[k]!.high > 0 ? `Remaining life ${result.rul[k]!.low}–${result.rul[k]!.high} days` : 'Replace now') : undefined} />
            ))}
          </Card>
          <Card>
            <Title>Quote</Title>
            {result.quote.lines.length ? result.quote.lines.map((l) => (
              <Row key={l.part} style={{ justifyContent: 'space-between' }}><Body>Replace {l.part}</Body><Body>₹{l.cost}</Body></Row>
            )) : <Muted>No part needs replacing.</Muted>}
            {result.quote.labour ? <Row style={{ justifyContent: 'space-between' }}><Muted>Labour</Muted><Muted>₹{result.quote.labour}</Muted></Row> : null}
            <Row style={{ justifyContent: 'space-between' }}><Muted>Visit</Muted><Muted>₹{result.quote.visit}</Muted></Row>
            <Row style={{ justifyContent: 'space-between', marginTop: 6 }}><Body style={{ fontWeight: '700' }}>Total</Body><Body style={{ fontWeight: '700' }}>₹{result.quote.total}</Body></Row>
          </Card>
          {job ? (
            <Card>
              {sent === 'sent' ? <Body>✓ Result sent to the server.</Body> : sent === 'queued' ? <Body>Saved on the phone; it uploads automatically when you are online.</Body> : null}
              {!sent ? <Button label="Send result to the server" onPress={submit} busy={busy} /> : null}
              {sent && job.status !== 'resolved' ? (
                <View style={{ marginTop: 10 }}>
                  <Button kind="secondary" label="Repair done: close the job" onPress={() => setStatus('resolved')} busy={busy} />
                </View>
              ) : null}
            </Card>
          ) : null}
        </>
      ) : null}
    </Screen>
  );
}

// Every value exactly as decoded from the last Bluetooth packet, with its sequence
// number and raw bytes: compare with the "[BLE] phone sees:" line on the ESP32 Serial Monitor.
function LiveDebug({ last, stats, info }: { last?: Reading; stats: { ok: number; bad: number; lastHex: string; lastAt: number } | null; info: string | null }) {
  const c = usePalette();
  const [open, setOpen] = useState(true);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const age = stats?.lastAt ? Math.round((Date.now() - stats.lastAt) / 1000) : null;
  const row = (k: string, v: string) => (
    <View key={k} style={{ flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 2 }}>
      <Muted>{k}</Muted><Text style={{ color: c.text, fontFamily: Platform.select({ android: 'monospace', ios: 'Menlo', default: 'monospace' }), fontSize: 13 }}>{v}</Text>
    </View>
  );
  return (
    <View style={{ marginTop: 12, borderWidth: 1, borderColor: c.border, borderRadius: 10, padding: 10 }}>
      <Pressable onPress={() => setOpen(!open)}>
        <Body style={{ fontWeight: '600' }}>Live data check {open ? '▾' : '▸'}</Body>
      </Pressable>
      {open ? (
        <>
          {stats ? row('packets ok / rejected', `${stats.ok} / ${stats.bad}`) : row('source', 'server relay')}
          {stats ? row('last packet', age === null ? 'none yet' : `${age} s ago`) : null}
          {info ? row('device', info) : null}
          {last ? (
            <>
              {row('seq', String(last.seq ?? '-'))}
              {row('flow', `${last.flow_rate_Lmin.toFixed(3)} L/min`)}
              {row('pump current', `${last.pump_current_A.toFixed(3)} A`)}
              {row('turbidity', `${last.turbidity_voltage_V.toFixed(3)} V`)}
              {row('TDS in', `${last.tds_in_ppm.toFixed(0)} ppm`)}
              {row('TDS out', `${last.tds_out_ppm.toFixed(1)} ppm`)}
              {row('pressure', `${last.pressure_bar.toFixed(3)} bar`)}
              {row('temperature', `${last.water_temp_C.toFixed(2)} C`)}
              {row('flags', `run=${last.running ? 1 : 0} wifi=${last.wifi ? 1 : 0} server=${last.server_ok ? 1 : 0}`)}
            </>
          ) : <Muted>Waiting for the first packet…</Muted>}
          {stats?.lastHex ? <Muted style={{ marginTop: 4 }}>bytes: {stats.lastHex}</Muted> : null}
        </>
      ) : null}
    </View>
  );
}

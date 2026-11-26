import { Link } from 'expo-router';
import { useState } from 'react';
import { Alert, Text, TextInput, View } from 'react-native';

import { Body, Button, Card, HealthRow, Muted, Pill, Row, Screen, Stat, STATUS_COLOR, Title } from '@/components/ui';
import { useSession } from '@/lib/session';
import { usePalette } from '@/lib/theme';
import { ACTIVE, COMPONENT_LABEL, JOB_LABEL, STATUS_LABEL, type ComponentKey, type Device, type Job } from '@/lib/types';

const STEPS: Job['status'][] = ['assigned', 'en_route', 'on_site', 'confirmed', 'resolved'];

function JobProgress({ job }: { job: Job }) {
  const c = usePalette();
  const at = job.status === 'no_fault' ? STEPS.length - 1 : Math.max(0, STEPS.indexOf(job.status));
  return (
    <Card style={{ borderLeftWidth: 5, borderLeftColor: c.accent }}>
      <Title>Service visit</Title>
      <Body>{job.hypothesis_label}</Body>
      {job.technician ? <Muted>Technician: {job.technician.name} · {job.technician.phone}</Muted> : <Muted>Finding a technician…</Muted>}
      <View style={{ marginTop: 10 }}>
        {STEPS.map((s, i) => (
          <Row key={s} style={{ marginVertical: 3 }}>
            <View style={{ width: 14, height: 14, borderRadius: 7, backgroundColor: i <= at ? c.accent : c.track }} />
            <Text style={{ color: i <= at ? c.text : c.textSecondary, fontWeight: i === at ? '700' : '400' }}>{JOB_LABEL[s]}</Text>
          </Row>
        ))}
      </View>
      {job.report ? (
        <View style={{ marginTop: 10 }}>
          <Body style={{ fontWeight: '600' }}>{job.report.verdict === 'no_fault' ? 'The technician found no fault.' : `Found on site: ${job.report.fault_label}`}</Body>
          <Muted>{job.report.reason}</Muted>
          {job.report.quote ? <Muted>Quote: ₹{job.report.quote.total}{job.report.quote.replace.length ? ` (${job.report.quote.replace.join(', ')})` : ''}</Muted> : null}
        </View>
      ) : job.estimate ? <Muted style={{ marginTop: 8 }}>Expected cost: about ₹{job.estimate.total}</Muted> : null}
    </Card>
  );
}

function Purifier({ d }: { d: Device }) {
  const c = usePalette();
  const { api, jobs } = useSession();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const r = d.latest;
  const job = Object.values(jobs).filter((j) => j.device_id === d.id && ACTIVE.includes(j.status)).sort((a, b) => b.id - a.id)[0];
  const lastDone = Object.values(jobs).filter((j) => j.device_id === d.id && !ACTIVE.includes(j.status)).sort((a, b) => b.id - a.id)[0];
  const rejection = r ? (1 - r.tds_out_ppm / Math.max(r.tds_in_ppm, 1)) * 100 : null;
  const issue = d.hypothesis && !['normal', 'idle'].includes(d.hypothesis.fault) && d.hypothesis.score >= 0.5;

  const book = async () => {
    setBusy(true);
    try {
      await api('/api/jobs', 'POST', { device_id: d.id, note: note || 'Booked from the AquaSense app' });
      setNote('');
    } catch (e) {
      Alert.alert('Could not book', e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <>
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
          <Title>{d.name}</Title>
          <Pill label={STATUS_LABEL[d.status]} color={STATUS_COLOR[d.status]} />
        </View>
        <Muted>{d.address} · {d.online ? '● live' : '○ offline'}</Muted>
        <Row style={{ marginTop: 12 }}>
          <Stat label="water flow" value={r ? r.flow_rate_Lmin.toFixed(2) : '–'} unit="L/min" />
          <Stat label="purified TDS" value={r ? r.tds_out_ppm.toFixed(0) : '–'} unit="ppm" />
          <Stat label="salt removed" value={rejection !== null ? rejection.toFixed(0) : '–'} unit="%" />
        </Row>
        <Row style={{ marginTop: 8 }}>
          <Stat label="source TDS" value={r ? r.tds_in_ppm.toFixed(0) : '–'} unit="ppm" />
          <Stat label="pump" value={r ? r.pump_current_A.toFixed(2) : '–'} unit="A" />
          <Stat label="water used" value={r ? r.total_volume_L.toFixed(0) : '–'} unit="L" />
        </Row>
        {issue ? <Body style={{ marginTop: 10, color: '#d03b3b' }}>We noticed a possible issue: {d.hypothesis!.label}. {job ? 'A technician has been assigned.' : ''}</Body> : null}
      </Card>
      {job ? <JobProgress job={job} /> : null}
      <Card>
        <Title>Component health</Title>
        {(Object.keys(d.health || {}) as ComponentKey[]).map((k) => <HealthRow key={k} name={COMPONENT_LABEL[k]} value={d.health[k]} />)}
        <Muted>Line at 40% = time to replace.</Muted>
      </Card>
      {!job ? (
        <Card>
          <Title>Need help?</Title>
          <TextInput value={note} onChangeText={setNote} placeholder="Describe the problem (optional)" placeholderTextColor={c.textSecondary}
            style={{ borderWidth: 1, borderColor: c.border, borderRadius: 10, padding: 10, color: c.text, marginBottom: 10 }} />
          <Button label="Book a service visit" onPress={book} busy={busy} />
          {lastDone?.report ? <Muted style={{ marginTop: 8 }}>Last visit: {lastDone.report.fault_label} · {JOB_LABEL[lastDone.status]}</Muted> : null}
        </Card>
      ) : null}
    </>
  );
}

export default function CustomerHome() {
  const { user, devices } = useSession();
  const list = Object.values(devices);
  return (
    <Screen title={`Hi, ${user?.name.split(' ')[0] ?? ''}`} right={<Link href="/settings"><Text style={{ fontSize: 20 }}>⚙️</Text></Link>}>
      {list.length ? list.map((d) => <Purifier key={d.id} d={d} />) : (
        <Card><Body>No purifier is linked to your account yet.</Body><Muted>The service desk links your purifier after installation.</Muted></Card>
      )}
    </Screen>
  );
}

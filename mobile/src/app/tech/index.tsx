import { Link, useRouter } from 'expo-router';
import { Pressable, Text, View } from 'react-native';

import { Body, Button, Card, Muted, Pill, Row, Screen, Title } from '@/components/ui';
import { useSession } from '@/lib/session';
import { usePalette } from '@/lib/theme';
import { ACTIVE, JOB_LABEL, type Job } from '@/lib/types';

const JOB_COLOR: Record<string, string> = {
  new: '#7a8494', assigned: '#2a78d6', en_route: '#2a78d6', on_site: '#ec835a', confirmed: '#d03b3b', no_fault: '#0ca30c', resolved: '#0ca30c', cancelled: '#7a8494',
};

function JobRow({ j }: { j: Job }) {
  const c = usePalette();
  const router = useRouter();
  return (
    <Pressable accessibilityRole="button" onPress={() => router.push(`/tech/job/${j.id}`)}>
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', gap: 8 }}>
          <Text style={{ color: c.text, fontWeight: '700', fontSize: 16, flex: 1 }}>#{j.id} · {j.hypothesis_label}</Text>
          <Pill label={JOB_LABEL[j.status]} color={JOB_COLOR[j.status]} />
        </View>
        <Body style={{ marginTop: 6 }}>{j.device?.name ?? j.device_id} · {j.device?.customer?.name ?? ''}</Body>
        <Muted>{j.device?.address}</Muted>
        <Muted>
          {j.source === 'quick_check' ? 'Raised by the server quick check' : j.source === 'customer' ? 'Booked by the customer' : 'From the service desk'} ·{' '}
          {new Date(j.created_at * 1000).toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' })}
        </Muted>
        {j.estimate ? <Muted>Carry: {j.estimate.part} · estimate ₹{j.estimate.total}</Muted> : null}
      </Card>
    </Pressable>
  );
}

export default function TechJobs() {
  const { user, jobs, pending, connected } = useSession();
  const list = Object.values(jobs).sort((a, b) => b.id - a.id);
  const active = list.filter((j) => ACTIVE.includes(j.status));
  const done = list.filter((j) => !ACTIVE.includes(j.status)).slice(0, 10);
  const router = useRouter();
  return (
    <Screen title={`Jobs · ${user?.name ?? ''}`} right={<Link href="/settings"><Text style={{ fontSize: 20 }}>⚙️</Text></Link>}>
      {!connected ? <Card><Muted>Offline: showing the last jobs received. Bluetooth checks still work; results upload when you are back online.</Muted></Card> : null}
      {pending.length ? <Card><Body>{pending.length} on-site result{pending.length > 1 ? 's' : ''} waiting to upload.</Body></Card> : null}
      <Row style={{ marginBottom: 12 }}>
        <View style={{ flex: 1 }}>
          <Button kind="secondary" label="Check a purifier nearby (Bluetooth)" onPress={() => router.push('/tech/job/0')} />
        </View>
      </Row>
      <Title>Active</Title>
      {active.length ? active.map((j) => <JobRow key={j.id} j={j} />) : <Muted style={{ marginBottom: 12 }}>No active jobs. New jobs appear here the moment the server assigns them.</Muted>}
      {done.length ? <Title>Recent</Title> : null}
      {done.map((j) => <JobRow key={j.id} j={j} />)}
    </Screen>
  );
}

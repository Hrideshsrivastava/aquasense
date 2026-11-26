import { useRouter } from 'expo-router';

import { Body, Button, Card, Muted, Screen, Title } from '@/components/ui';
import { bleSupported } from '@/lib/ble';
import { useSession } from '@/lib/session';

export default function Settings() {
  const router = useRouter();
  const { user, server, connected, logout, pending } = useSession();
  return (
    <Screen title="Settings" back>
      <Card>
        <Title>{user?.name}</Title>
        <Muted>{user?.role === 'technician' ? 'Technician' : 'Customer'} · {user?.username}</Muted>
      </Card>
      <Card>
        <Body>Server: {server}</Body>
        <Muted>{connected ? 'Connected, live updates on' : 'Not connected: retrying every few seconds'}</Muted>
        <Muted>Bluetooth: {bleSupported ? 'available' : 'not available in this build'}</Muted>
        {pending.length ? <Muted>{pending.length} on-site result(s) waiting to upload</Muted> : null}
      </Card>
      <Button kind="secondary" label="Sign out" onPress={async () => { await logout(); router.replace('/login'); }} />
    </Screen>
  );
}

import { useState } from 'react';
import { Linking, Platform, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, Card, Muted, Title } from '@/components/ui';
import { api, inr, type Ticket } from '@/lib/api';
import { usePolling, useServer } from '@/lib/server';
import { usePalette } from '@/lib/theme';

// Technician view: service tickets and job cards created by the console
// ("Contact customer", Scenario 1) or booked by customers.
export default function Service() {
  const c = usePalette();
  const { server } = useServer();
  const tickets = usePolling(() => api.tickets(server), 8000, [server]);
  const [open, setOpen] = useState<number | null>(null);

  const set = async (t: Ticket, status: Ticket['status']) => {
    await api.setTicket(server, t.id, status);
    tickets.refresh();
  };

  const list = tickets.data ?? [];
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.background }} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page}>
        <Title>Service jobs</Title>
        <Muted style={{ marginBottom: 12 }}>
          Each job card names the likely fault and the part to carry, so the right part arrives first time.
        </Muted>
        {tickets.error && !tickets.data ? <Muted>Cannot reach {server}: {tickets.error}</Muted> : null}
        {tickets.data && list.length === 0 ? <Muted>No service jobs yet.</Muted> : null}
        {list.map((t) => (
          <Card key={t.id}>
            <View style={styles.head}>
              <Body style={{ fontWeight: '700' }}>
                #{t.id} · {t.unit_id}
              </Body>
              <Muted>{t.status.toUpperCase()}</Muted>
            </View>
            <Body>{t.fault}</Body>
            <Muted>
              {t.customer} · {t.city} · {t.visit_window}
            </Muted>
            <Muted>
              Carry: {t.part_to_carry} · estimate {inr(t.price_estimate)}
            </Muted>
            <View style={styles.actions}>
              <View style={{ flex: 1 }}>
                <Button kind="secondary" label={open === t.id ? 'Hide job card' : 'Job card'} onPress={() => setOpen(open === t.id ? null : t.id)} />
              </View>
              {t.status === 'open' ? (
                <View style={{ flex: 1 }}>
                  <Button kind="secondary" label="Scheduled" onPress={() => set(t, 'scheduled')} />
                </View>
              ) : null}
              {t.status !== 'done' ? (
                <View style={{ flex: 1 }}>
                  <Button label="Done" onPress={() => set(t, 'done')} />
                </View>
              ) : null}
            </View>
            {t.phone && Platform.OS !== 'web' ? (
              <View style={{ marginTop: 8 }}>
                <Button kind="secondary" label={`Call ${t.phone}`} onPress={() => Linking.openURL(`tel:${t.phone.replace(/\s/g, '')}`)} />
              </View>
            ) : null}
            {open === t.id ? (
              <ScrollView horizontal style={[styles.jobcard, { backgroundColor: c.background, borderColor: c.border }]}>
                <Text style={[styles.mono, { color: c.text }]}>{t.job_card}</Text>
              </ScrollView>
            ) : null}
          </Card>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { padding: 16, paddingBottom: 40, maxWidth: 720, width: '100%', alignSelf: 'center' },
  head: { flexDirection: 'row', justifyContent: 'space-between' },
  actions: { flexDirection: 'row', gap: 8, marginTop: 10 },
  jobcard: { marginTop: 10, borderWidth: 1, borderRadius: 8, padding: 10 },
  mono: { fontFamily: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' }), fontSize: 11 },
});

import { useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, Card, HealthRow, Muted, StatusPill, Title } from '@/components/ui';
import { api, inr, type FleetUnit, type Ticket } from '@/lib/api';
import { usePolling, useServer } from '@/lib/server';
import { usePalette } from '@/lib/theme';

// Customer view: "works like a connected-car tracker" (abstract, Fig. 5).
export default function MyPurifier() {
  const c = usePalette();
  const { server, unitId, setUnitId } = useServer();
  const units = usePolling(() => api.units(server), 10000, [server]);
  const selected = unitId ?? units.data?.find((u) => !u.simulated)?.id ?? units.data?.[0]?.id ?? null;
  const detail = usePolling(
    () => (selected ? api.unit(server, selected) : Promise.resolve(null)),
    5000,
    [server, selected],
  );
  const [booked, setBooked] = useState<Ticket | null>(null);
  const [busy, setBusy] = useState(false);
  const [bookError, setBookError] = useState<string | null>(null);

  const book = async () => {
    if (!selected) return;
    setBusy(true);
    setBookError(null);
    try {
      setBooked(await api.bookService(server, selected, 'Booked by the customer from the AquaSense app'));
    } catch (e) {
      setBookError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const d = detail.data;
  const a = d?.assessment;
  const openAlert = d?.alerts.find((x) => x.status === 'open');

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.background }} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page}>
        <Text style={[styles.brand, { color: c.accent }]}>
          AquaSense <Text style={{ color: c.textSecondary, fontSize: 15, fontWeight: '400' }}>· My purifier</Text>
        </Text>

        {units.error && !units.data ? (
          <Card>
            <Body>Cannot reach the AquaSense cloud at {server}.</Body>
            <Muted>Start it with `python -m cloud.server` and set the address in Settings.</Muted>
          </Card>
        ) : null}

        {units.data && units.data.length > 1 ? (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 12 }}>
            {units.data.map((u: FleetUnit) => (
              <Pressable
                key={u.id}
                onPress={() => {
                  setUnitId(u.id);
                  setBooked(null);
                }}
                style={[
                  styles.chip,
                  { borderColor: u.id === selected ? c.accent : c.border, backgroundColor: c.card },
                ]}>
                <Text style={{ color: c.text, fontWeight: u.id === selected ? '700' : '400' }}>
                  {u.id}
                  {u.simulated ? ' (sim)' : ''}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        ) : null}

        {detail.loading && !d ? <ActivityIndicator /> : null}

        {d && a ? (
          <>
            <Card>
              <View style={styles.headRow}>
                <Title>{d.unit.name}</Title>
                <Muted>{d.unit.simulated ? 'SIMULATED' : d.online ? '● CONNECTED' : '○ OFFLINE'}</Muted>
              </View>
              <Muted>
                {d.unit.customer} · {d.unit.city} · {d.unit.water_source} water
              </Muted>
              {a.status === 'no_data' ? (
                <Body style={{ marginTop: 8 }}>{a.message}</Body>
              ) : (
                <View style={{ marginTop: 10, gap: 6 }}>
                  <StatusPill status={a.status} />
                  <Body style={{ fontWeight: '600' }}>
                    {a.fault === 'normal' ? 'All components healthy' : a.fault_name}
                  </Body>
                  <Muted>
                    Source water {a.tds_in.toFixed(0)} ppm · purified water rejection {a.rejection_pct.toFixed(1)}%
                  </Muted>
                </View>
              )}
            </Card>

            {a.status !== 'no_data' ? (
              <>
                <Card>
                  <Body style={{ fontWeight: '700', marginBottom: 4 }}>Component health</Body>
                  {a.components.map((comp) => (
                    <HealthRow key={comp.component} comp={comp} />
                  ))}
                  <Muted>Line at 40% = replacement threshold. Life estimates use simulated degradation.</Muted>
                </Card>

                {openAlert || a.fault !== 'normal' ? (
                  <Card style={{ borderLeftWidth: 5, borderLeftColor: '#fab219' }}>
                    <Body style={{ fontWeight: '700' }}>Alert: {openAlert?.fault_name ?? a.fault_name}</Body>
                    <Body>{a.reason}</Body>
                    {a.evidence.slice(0, 3).map((e) => (
                      <Muted key={e}>• {e}</Muted>
                    ))}
                  </Card>
                ) : null}

                <Card>
                  <Body style={{ fontWeight: '700' }}>Estimated service cost</Body>
                  {a.quote.replace_now.length === 0 ? (
                    <Muted>No part needs replacing now. Routine visit: {inr(a.quote.visit_charge)}</Muted>
                  ) : (
                    <>
                      {a.quote.replace_now.map((l) => (
                        <View key={l.part} style={styles.priceRow}>
                          <Body>{l.part}</Body>
                          <Body>{inr(l.cost)}</Body>
                        </View>
                      ))}
                      <View style={styles.priceRow}>
                        <Muted>Labour + visit</Muted>
                        <Muted>{inr(a.quote.labour + a.quote.visit_charge)}</Muted>
                      </View>
                      <View style={styles.priceRow}>
                        <Body style={{ fontWeight: '700' }}>Total</Body>
                        <Body style={{ fontWeight: '700' }}>{inr(a.quote.repair_price_now)}</Body>
                      </View>
                    </>
                  )}
                  <View style={{ marginTop: 12 }}>
                    <Button label={busy ? 'Booking…' : 'Book service'} onPress={book} disabled={busy} />
                  </View>
                  {booked ? (
                    <Muted style={{ marginTop: 8 }}>
                      Booked: ticket #{booked.id} · {booked.visit_window} · technician brings {booked.part_to_carry}
                    </Muted>
                  ) : null}
                  {bookError ? <Muted style={{ marginTop: 8, color: '#d03b3b' }}>{bookError}</Muted> : null}
                </Card>
              </>
            ) : null}
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { padding: 16, paddingBottom: 40, maxWidth: 720, width: '100%', alignSelf: 'center' },
  brand: { fontSize: 26, fontWeight: '800', marginBottom: 12 },
  chip: { borderWidth: 1, borderRadius: 99, paddingHorizontal: 12, paddingVertical: 6, marginRight: 8 },
  headRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  priceRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
});

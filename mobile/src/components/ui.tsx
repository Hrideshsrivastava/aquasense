import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View, type ViewStyle } from 'react-native';

import type { Component, Status } from '@/lib/api';
import { rulText } from '@/lib/api';
import { StatusColor, StatusLabel, usePalette } from '@/lib/theme';

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  const c = usePalette();
  return <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }, style]}>{children}</View>;
}

export function Title({ children }: { children: ReactNode }) {
  const c = usePalette();
  return <Text style={[styles.title, { color: c.text }]}>{children}</Text>;
}

export function Muted({ children, style }: { children: ReactNode; style?: object }) {
  const c = usePalette();
  return <Text style={[styles.muted, { color: c.textSecondary }, style]}>{children}</Text>;
}

export function Body({ children, style }: { children: ReactNode; style?: object }) {
  const c = usePalette();
  return <Text style={[styles.body, { color: c.text }, style]}>{children}</Text>;
}

/** Status is always colour + text, never colour alone. */
export function StatusPill({ status }: { status: Status }) {
  const bg = StatusColor[status];
  return (
    <View style={[styles.pill, { backgroundColor: bg }]}>
      <Text style={[styles.pillText, { color: status === 'warning' ? '#111' : '#fff' }]}>{StatusLabel[status]}</Text>
    </View>
  );
}

export function HealthRow({ comp }: { comp: Component }) {
  const c = usePalette();
  const pct = Math.max(0, Math.min(100, comp.health));
  const verdict = comp.decision === 'replace' ? 'replace' : comp.decision === 'watch' ? 'watch' : 'ok';
  const sub = comp.component === 'sensors' ? `fault probability ${comp.failure_prob.toFixed(2)}` : `Est. life left ${rulText(comp.rul)}`;
  return (
    <View style={styles.row} accessible accessibilityLabel={`${comp.name} ${pct.toFixed(0)} percent, ${verdict}`}>
      <View style={styles.rowHead}>
        <Text style={[styles.rowName, { color: c.text }]}>{comp.name}</Text>
        <Text style={[styles.rowValue, { color: c.text }]}>
          {pct.toFixed(0)}% · {verdict}
        </Text>
      </View>
      <View style={[styles.track, { backgroundColor: c.track }]}>
        <View style={[styles.fill, { width: `${pct}%`, backgroundColor: StatusColor[comp.status] }]} />
        <View style={[styles.threshold, { left: '40%', backgroundColor: c.textSecondary }]} />
      </View>
      <Text style={[styles.muted, { color: c.textSecondary }]}>{sub}</Text>
    </View>
  );
}

export function Button({ label, onPress, kind = 'primary', disabled }: {
  label: string;
  onPress: () => void;
  kind?: 'primary' | 'secondary';
  disabled?: boolean;
}) {
  const c = usePalette();
  const primary = kind === 'primary';
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: primary ? c.accent : c.card,
          borderColor: primary ? c.accent : c.border,
          opacity: disabled ? 0.5 : pressed ? 0.8 : 1,
        },
      ]}>
      <Text style={[styles.buttonText, { color: primary ? c.accentText : c.text }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: { borderRadius: 14, borderWidth: 1, padding: 16, marginBottom: 12 },
  title: { fontSize: 20, fontWeight: '700', marginBottom: 4 },
  muted: { fontSize: 13, lineHeight: 18 },
  body: { fontSize: 15, lineHeight: 21 },
  pill: { alignSelf: 'flex-start', borderRadius: 99, paddingHorizontal: 10, paddingVertical: 3 },
  pillText: { fontSize: 12, fontWeight: '700' },
  row: { paddingVertical: 8 },
  rowHead: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
  rowName: { fontSize: 15, fontWeight: '600' },
  rowValue: { fontSize: 14, fontWeight: '600' },
  track: { height: 10, borderRadius: 5, overflow: 'hidden', marginBottom: 4 },
  fill: { height: '100%', borderRadius: 5 },
  threshold: { position: 'absolute', top: 0, bottom: 0, width: 1 },
  button: { borderRadius: 10, borderWidth: 1, paddingVertical: 12, paddingHorizontal: 16, alignItems: 'center' },
  buttonText: { fontSize: 16, fontWeight: '600' },
});

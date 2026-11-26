import type { ReactNode } from 'react';
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, View, type ViewStyle } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';

import { useSession } from '@/lib/session';
import { usePalette } from '@/lib/theme';

export const STATUS_COLOR: Record<string, string> = {
  normal: '#0ca30c', learning: '#7a8494', idle: '#7a8494', suspect: '#ec835a', alert: '#d03b3b', service: '#2a78d6', offline: '#7a8494',
};
export const healthColor = (h: number) => (h < 40 ? '#d03b3b' : h < 70 ? '#fab219' : '#0ca30c');

export function Screen({ title, back, right, children, scroll = true }: { title: string; back?: boolean; right?: ReactNode; children: ReactNode; scroll?: boolean }) {
  const c = usePalette();
  const router = useRouter();
  const { connected } = useSession();
  const body = scroll ? <ScrollView contentContainerStyle={styles.page}>{children}</ScrollView> : <View style={[styles.page, { flex: 1 }]}>{children}</View>;
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.background }} edges={['top']}>
      <View style={[styles.header, { borderBottomColor: c.border, backgroundColor: c.card }]}>
        {back ? (
          <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={() => router.back()} hitSlop={12}>
            <Text style={{ color: c.accent, fontSize: 16, fontWeight: '600' }}>‹ Back</Text>
          </Pressable>
        ) : (
          <Text style={{ color: c.accent, fontSize: 20, fontWeight: '800' }}>AquaSense</Text>
        )}
        <Text style={[styles.headerTitle, { color: c.text }]} numberOfLines={1}>{title}</Text>
        <View style={styles.headerRight}>
          {right}
          <View accessibilityLabel={connected ? 'Live' : 'Offline'} style={[styles.dot, { backgroundColor: connected ? '#0ca30c' : '#7a8494' }]} />
        </View>
      </View>
      {body}
    </SafeAreaView>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  const c = usePalette();
  return <View style={[styles.card, { backgroundColor: c.card, borderColor: c.border }, style]}>{children}</View>;
}
export function Title({ children }: { children: ReactNode }) {
  const c = usePalette();
  return <Text style={[styles.title, { color: c.text }]}>{children}</Text>;
}
export function Body({ children, style }: { children: ReactNode; style?: object }) {
  const c = usePalette();
  return <Text style={[styles.body, { color: c.text }, style]}>{children}</Text>;
}
export function Muted({ children, style }: { children: ReactNode; style?: object }) {
  const c = usePalette();
  return <Text style={[styles.muted, { color: c.textSecondary }, style]}>{children}</Text>;
}
export function Pill({ label, color, dark }: { label: string; color: string; dark?: boolean }) {
  return (
    <View style={[styles.pill, { backgroundColor: color }]}>
      <Text style={{ color: dark ? '#111' : '#fff', fontSize: 12, fontWeight: '700' }}>{label}</Text>
    </View>
  );
}
export function HealthRow({ name, value, sub }: { name: string; value: number; sub?: string }) {
  const c = usePalette();
  return (
    <View style={styles.hrow} accessible accessibilityLabel={`${name} ${Math.round(value)} percent`}>
      <View style={styles.between}>
        <Text style={{ color: c.text, fontWeight: '600', fontSize: 15 }}>{name}</Text>
        <Text style={{ color: c.text, fontWeight: '700' }}>{Math.round(value)}%</Text>
      </View>
      <View style={[styles.track, { backgroundColor: c.track }]}>
        <View style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', borderRadius: 5, backgroundColor: healthColor(value) }} />
        <View style={[styles.threshold, { backgroundColor: c.textSecondary }]} />
      </View>
      {sub ? <Text style={[styles.muted, { color: c.textSecondary }]}>{sub}</Text> : null}
    </View>
  );
}
export function Stat({ label, value, unit }: { label: string; value: string; unit?: string }) {
  const c = usePalette();
  return (
    <View style={[styles.stat, { backgroundColor: c.background }]}>
      <Text style={{ color: c.text, fontSize: 18, fontWeight: '700' }}>{value}<Text style={{ fontSize: 12, color: c.textSecondary }}>{unit ? ` ${unit}` : ''}</Text></Text>
      <Text style={{ color: c.textSecondary, fontSize: 11 }}>{label}</Text>
    </View>
  );
}
export function Button({ label, onPress, kind = 'primary', disabled, busy }: {
  label: string; onPress: () => void; kind?: 'primary' | 'secondary' | 'danger'; disabled?: boolean; busy?: boolean;
}) {
  const c = usePalette();
  const bg = kind === 'primary' ? c.accent : kind === 'danger' ? '#d03b3b' : c.card;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [styles.button, { backgroundColor: bg, borderColor: kind === 'secondary' ? c.border : bg, opacity: disabled ? 0.5 : pressed ? 0.8 : 1 }]}>
      {busy ? <ActivityIndicator color={kind === 'secondary' ? c.text : '#fff'} /> : (
        <Text style={[styles.buttonText, { color: kind === 'secondary' ? c.text : '#fff' }]}>{label}</Text>
      )}
    </Pressable>
  );
}
export function Row({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.row, style]}>{children}</View>;
}

export const styles = StyleSheet.create({
  page: { padding: 16, paddingBottom: 48, maxWidth: 720, width: '100%', alignSelf: 'center' },
  header: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 12, borderBottomWidth: 1 },
  headerTitle: { flex: 1, fontSize: 16, fontWeight: '600' },
  headerRight: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  card: { borderRadius: 14, borderWidth: 1, padding: 16, marginBottom: 12 },
  title: { fontSize: 19, fontWeight: '700', marginBottom: 4 },
  body: { fontSize: 15, lineHeight: 21 },
  muted: { fontSize: 13, lineHeight: 18 },
  pill: { alignSelf: 'flex-start', borderRadius: 99, paddingHorizontal: 10, paddingVertical: 3 },
  hrow: { paddingVertical: 7 },
  between: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  track: { height: 10, borderRadius: 5, overflow: 'hidden', marginVertical: 5 },
  threshold: { position: 'absolute', left: '40%', top: 0, bottom: 0, width: 1 },
  stat: { flex: 1, borderRadius: 10, padding: 10 },
  button: { borderRadius: 11, borderWidth: 1, paddingVertical: 13, paddingHorizontal: 16, alignItems: 'center', minHeight: 48, justifyContent: 'center' },
  buttonText: { fontSize: 16, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 8, alignItems: 'center' },
});

import { useState } from 'react';
import { ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, Card, Muted, Title } from '@/components/ui';
import { api } from '@/lib/api';
import { useServer } from '@/lib/server';
import { usePalette } from '@/lib/theme';

export default function Settings() {
  const c = usePalette();
  const { server, setServer, setUnitId } = useServer();
  const [draft, setDraft] = useState(server);
  const [result, setResult] = useState<string | null>(null);

  const test = async () => {
    const url = draft.trim();
    setServer(url);
    setUnitId(null);
    setResult('Connecting…');
    try {
      const h = await api.health(url);
      const m = h.model as { data_source?: string; trained_at?: string; macro_f1_with_cross_check?: number };
      setResult(`Connected. Models: ${m.data_source ?? '?'} (trained ${m.trained_at ?? '?'}), macro F1 ${m.macro_f1_with_cross_check ?? '?'}`);
    } catch (e) {
      setResult(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.background }} edges={['top']}>
      <ScrollView contentContainerStyle={styles.page}>
        <Title>Settings</Title>
        <Card>
          <Body style={{ fontWeight: '600' }}>AquaSense cloud server</Body>
          <Muted>The laptop running `python -m cloud.server`, on the same hotspot as this phone.</Muted>
          <TextInput
            value={draft}
            onChangeText={setDraft}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            placeholder="http://192.168.43.100:5000"
            placeholderTextColor={c.textSecondary}
            style={[styles.input, { color: c.text, borderColor: c.border, backgroundColor: c.background }]}
          />
          <View style={{ marginTop: 10 }}>
            <Button label="Save and test" onPress={test} />
          </View>
          {result ? <Muted style={{ marginTop: 8 }}>{result}</Muted> : null}
        </Card>
        <Card>
          <Body style={{ fontWeight: '600' }}>About</Body>
          <Muted>
            AquaSense prototype by Team Clusters. Component health and fault diagnosis come from the same models as the
            company console and the technician handheld. Remaining-life ranges use simulated degradation data, and are
            an estimate, not a water-safety certification.
          </Muted>
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { padding: 16, paddingBottom: 40, maxWidth: 720, width: '100%', alignSelf: 'center' },
  input: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, marginTop: 10, fontSize: 15 },
});

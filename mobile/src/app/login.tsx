import { useRouter } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Body, Button, Card, Muted } from '@/components/ui';
import { useSession } from '@/lib/session';
import { usePalette } from '@/lib/theme';

export default function Login() {
  const c = usePalette();
  const router = useRouter();
  const { server, setServer, login } = useSession();
  const [srv, setSrv] = useState(server);
  const [username, setUsername] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await setServer(srv);
      const u = await login(username.trim(), pin.trim());
      router.replace(u.role === 'technician' ? '/tech' : '/customer');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const input = [styles.input, { color: c.text, borderColor: c.border, backgroundColor: c.card }];
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: c.background }}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.page} keyboardShouldPersistTaps="handled">
          <Text style={[styles.brand, { color: c.accent }]}>AquaSense</Text>
          <Muted style={{ marginBottom: 20 }}>Sign in as a customer to see your purifier live, or as a technician to see your jobs.</Muted>
          <Card>
            <Text style={[styles.label, { color: c.textSecondary }]}>Username</Text>
            <TextInput value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} placeholder="e.g. ravi or kavya"
              placeholderTextColor={c.textSecondary} style={input} accessibilityLabel="Username" />
            <Text style={[styles.label, { color: c.textSecondary }]}>PIN</Text>
            <TextInput value={pin} onChangeText={setPin} secureTextEntry keyboardType="number-pad" placeholder="4-digit PIN"
              placeholderTextColor={c.textSecondary} style={input} accessibilityLabel="PIN" onSubmitEditing={submit} />
            <Text style={[styles.label, { color: c.textSecondary }]}>Server</Text>
            <TextInput value={srv} onChangeText={setSrv} autoCapitalize="none" autoCorrect={false} keyboardType="url"
              placeholder="http://192.168.1.100:5000" placeholderTextColor={c.textSecondary} style={input} accessibilityLabel="Server address" />
            <View style={{ marginTop: 16 }}>
              <Button label="Sign in" onPress={submit} busy={busy} disabled={!username || !pin} />
            </View>
            {error ? <Body style={{ color: '#d03b3b', marginTop: 10 }}>{error}</Body> : null}
          </Card>
          <Muted>The server address is the laptop running the AquaSense server, on the same Wi-Fi as this phone.</Muted>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { padding: 20, paddingTop: 48, maxWidth: 480, width: '100%', alignSelf: 'center' },
  brand: { fontSize: 34, fontWeight: '800', marginBottom: 6 },
  label: { fontSize: 13, fontWeight: '600', marginTop: 10, marginBottom: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11, fontSize: 16 },
});

import { DarkTheme, DefaultTheme, ThemeProvider } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';
import { StatusBar } from 'expo-status-bar';
import { Text, useColorScheme, type ColorValue } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ServerProvider } from '@/lib/server';
import { usePalette } from '@/lib/theme';

function TabIcon({ glyph, color }: { glyph: string; color: ColorValue }) {
  return <Text style={{ color, fontSize: 18 }}>{glyph}</Text>;
}

export default function RootLayout() {
  const scheme = useColorScheme();
  const c = usePalette();
  return (
    <SafeAreaProvider>
      <ServerProvider>
        <ThemeProvider value={scheme === 'dark' ? DarkTheme : DefaultTheme}>
          <StatusBar style="auto" />
          <Tabs
            screenOptions={{
              headerShown: false,
              tabBarActiveTintColor: c.accent,
              tabBarStyle: { backgroundColor: c.card, borderTopColor: c.border },
            }}>
            <Tabs.Screen
              name="index"
              options={{ title: 'My purifier', tabBarIcon: ({ color }) => <TabIcon glyph="💧" color={color} /> }}
            />
            <Tabs.Screen
              name="service"
              options={{ title: 'Technician', tabBarIcon: ({ color }) => <TabIcon glyph="🔧" color={color} /> }}
            />
            <Tabs.Screen
              name="settings"
              options={{ title: 'Settings', tabBarIcon: ({ color }) => <TabIcon glyph="⚙️" color={color} /> }}
            />
          </Tabs>
        </ThemeProvider>
      </ServerProvider>
    </SafeAreaProvider>
  );
}

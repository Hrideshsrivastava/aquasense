import { Redirect } from 'expo-router';
import { ActivityIndicator, View } from 'react-native';

import { useSession } from '@/lib/session';

export default function Index() {
  const { ready, user } = useSession();
  if (!ready) {
    return (
      <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
        <ActivityIndicator />
      </View>
    );
  }
  if (!user) return <Redirect href="/login" />;
  return <Redirect href={user.role === 'technician' ? '/tech' : '/customer'} />;
}

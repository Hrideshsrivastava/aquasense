# AquaSense mobile app (Expo)

Companion app for the AquaSense prototype, built with the same stack as
MuscleSense (Expo SDK 57, expo-router, React Native 0.86). It talks to the
AquaSense cloud server (`python -m cloud.server`) over the phone hotspot.

| Tab | What it shows |
|---|---|
| **My purifier** | Customer view ("connected-car tracker"): status, component health bars with the 40 % threshold, estimated life left, alert with the reason, estimated service cost, **Book service** (creates a ticket + job card on the server) |
| **Technician** | Service jobs from the console's *Contact customer* button or from customers: fault, part to carry, estimate, job card, mark scheduled/done |
| **Settings** | Cloud server address + connection test |

## Run

```bash
cd mobile
npm install
EXPO_PUBLIC_AQUASENSE_SERVER=http://<laptop-ip>:5000 npx expo start
```

Open it in Expo Go, a development build, or press `w` for the web version.
The server address can also be changed in the Settings tab.

* The laptop and the phone must be on the same network (the demo hotspot).
* Android allows plain `http://` to the laptop through `expo-build-properties`
  (`usesCleartextTraffic`); iOS through `NSAllowsLocalNetworking`. Fine for a
  hotspot demo; use HTTPS for anything real.

Checks: `npm run typecheck` and `npm run export:web`.

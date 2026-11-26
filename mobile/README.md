# AquaSense Android app

One app, two roles (sign in decides): **technician** and **customer**. Built with
the same stack as MuscleSense (Expo SDK 57, expo-router, react-native-ble-plx).

* Customer: purifier live (WebSocket), component health, service-visit progress, book a visit.
* Technician: jobs pushed in real time; on site, connect to the ESP32 over Bluetooth
  (`AquaSense-XXXXXX`), run the **full diagnosis on the phone** (`src/lib/diagnosis.ts`),
  send the result (queued if offline) and close the job.

## Get the APK

GitHub Actions builds it on every push (workflow *Android APK*): download
`AquaSense.apk` from the **android-latest** release or the run's artifacts.

## Run from source

```bash
npm install
npx expo run:android          # phone connected over USB, Android SDK installed
```

Bluetooth uses a native module, so Expo Go cannot run it; use the APK or a
development build. `npx expo start --web` runs the same screens in a browser,
where the on-site check uses the server's live readings instead of Bluetooth.

Checks: `npm run typecheck`, `npm run export:web`.
Server setup and the end-to-end flow: `../docs/live_system.md`.

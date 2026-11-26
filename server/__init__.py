"""AquaSense company server (v2, live devices).

ESP32 purifiers post readings over Wi-Fi; the server runs a light "quick
check" on every device, raises alerts, assigns a technician and pushes every
change in real time (WebSocket) to the company dashboard and the Android app.
The technician confirms on site over Bluetooth with the full diagnosis that
runs on the phone.
"""

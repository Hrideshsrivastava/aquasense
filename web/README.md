# AquaSense live demo (static website)

A self-contained demo of the whole prototype that runs in the browser, with no
server and no hardware. It is what to host on Vercel.

| Screen | What it shows |
|---|---|
| Overview | the idea, architecture, both scenarios with one-click guided tours |
| Rig (Unit A) | fault-injection panel (CLOG / PWM / BYPASS levels, sensor faults, salt, hotspot), live water loop, OLED + LEDs, SD-card log with unsent lines |
| Console (Unit C) | fleet view, unit detail with live charts, component health, RUL chart, alert after 3 confident windows, **Contact customer** → job card, alerts & tickets, warranty & parts |
| Handheld (Unit B) | Raspberry Pi screen: move the SD card over, scan offline, health / fault / quote / unit tabs, save the quote |
| Customer app | phone view of the companion app: purifier status, Book service, technician jobs |
| Models (Unit D) | model card, confusion matrices, honest-evaluation notes |

How it works: `js/engine.js` is a JavaScript port of the Python prototype's rig
model, window features, cross-check diagnosis, RUL and pricing; `js/world.js`
runs the rig second by second with its SD card and Wi-Fi sync, the cloud
(scoring, alerts, tickets) and six simulated fleet units. The simulation speed
(1× to 60×) is in the top bar.

The ML numbers on the Models page come from `models/model_card.json` and
`reports/training_report.md` (synthetic training data, stated on the page).

## Run locally

Any static server, for example:

```bash
cd web
python3 -m http.server 8000
```

Then open http://localhost:8000. (Opening `index.html` straight from disk does not
work, because browsers block ES modules on `file://`.)

## Deploy on Vercel

Option A (simplest): import the GitHub repository in Vercel, then in
**Project Settings → Build & Deployment** set **Root Directory** to `web`,
Framework preset **Other**, no build command. Deploy.

Option B: keep the repository root as the project root. The root `vercel.json`
already sets `outputDirectory: "web"` with no install or build step.

Option C (CLI):

```bash
cd web
npx vercel --prod
```

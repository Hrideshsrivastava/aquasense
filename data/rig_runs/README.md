# Real rig recordings go here

1. Record labelled runs on the rig in training mode (hold the label button 2 s,
   short-press to pick the class shown on the OLED). Follow
   `docs/data_collection_protocol.md`.
2. Copy the day files (`YYYY-MM-DD.csv`) from the SD card into this folder.
3. Fill `logbook.csv` (template: `docs/logbook_template.csv`) with one line per
   run: `run_id,start,end,class,level,salt_ppm,sensor_mode,notes`.
4. Train on the real data: `python -m ml.train --real data/rig_runs`

The CSV files are git-ignored; keep a backup of the card.

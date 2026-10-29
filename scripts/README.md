# Scripts

- `demo.py` — walks the STAR-Net mechanism end to end against a running API and asserts the
  acceptance criteria. Run it after `docker compose up`:

  ```bash
  python scripts/demo.py --base http://localhost:8000
  ```

  It creates a fresh reporter account each run and jitters the fault's location by a few hundred
  metres, so it is safe to run repeatedly — filing at the identical spot would cluster onto the
  previous run's issue instead of opening a new one.

  The photos it posts carry EXIF capture time and GPS, which is what the mobile app's live camera
  sends and what the server's geo-coherence check reads.

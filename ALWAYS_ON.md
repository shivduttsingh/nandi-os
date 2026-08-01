# Free local always-on Nandi

This mode runs two local services on your own computer:

- `dashboard` — open it at `http://localhost:8501` to see evidence, saved decisions and alerts.
- `worker` — checks the NSE session in IST, captures the Upstox NIFTY option chain every 30 seconds during regular market hours, and writes its paper-research results to the same local database.

The worker never calls a broker-order endpoint. It cannot place trades.

## One-time setup

1. Install Docker Desktop on the computer that will stay on during market hours.
2. Download or clone this repository.
3. Create the local settings file:

   **Windows PowerShell**

   ```powershell
   Copy-Item .env.example .env
   ```

   **macOS/Linux**

   ```bash
   cp .env.example .env
   ```

4. Open `.env` in a text editor and set your private username/password and read-only Upstox Analytics Token. Do not send that file to anyone.
5. Add the official NSE holidays you want Nandi to skip in `NANDI_NSE_HOLIDAYS`, as comma-separated `YYYY-MM-DD` values.
6. Start Nandi:

   ```bash
   docker compose up -d --build
   ```

7. Open `http://localhost:8501` and log in with the username and password from `.env`.

## What Nandi follows

- Timezone: `Asia/Kolkata` (IST), never the computer/server timezone.
- Regular NSE equity-derivatives session: 09:15 to 15:30 IST, Monday to Friday.
- Weekends and configured NSE holidays: no live option-chain capture.
- Market open: data capture every 30 seconds by default.
- Market close: a saved daily summary, with all snapshots, setup quality and explanations.
- Upstox temporary error: worker records `DEGRADED` and retries with an increasing delay.

## Check that it is running

```bash
docker compose ps
docker compose logs -f worker
```

The Command Center will show the worker heartbeat, last snapshot, market state and latest saved analysis. The Daily Report shows the saved chart history and alerts.

## Stop or restart

```bash
docker compose down
docker compose up -d
```

Do not use `docker compose down -v`: that removes the saved Nandi database.

## Alerts

Nandi always saves approved-setup and daily-report alerts in the local dashboard. To receive an alert outside the dashboard, set `NANDI_ALERT_WEBHOOK_URL` to a notification endpoint you control. Nandi sends only a title, explanation, score and timestamp; it never sends your Upstox token.

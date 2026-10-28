# SMRI Attendance Logging Backend

A small Linux-native attendance server for eSSL/ZKTeco-style ADMS push devices. It intentionally stores raw punches and device-created employees only; it does not calculate attendance, shifts, leave, payroll, or overtime.

## Safety model

- A newly connected device is registered passively by serial number.
- Employee IDs are scoped to the reporting device, not globally. Employee `1` on one device is unrelated to employee `1` on another.
- Connecting a device never sends a command automatically.
- The UI has no employee create, update, push, or delete action.
- Only read/status and set-time command presets can be queued.
- There are no clear-data, clear-users, or bulk-replacement commands.
- Retried punch uploads are deduplicated.

Changing the old device server address to this service will therefore not erase or overwrite employees.

## Supported device routes

All routes accept the extensionless, `.aspx`, and `.php` forms used by the old server:

- `/iclock/cdata` — options, punches, operation logs, users and biometric data
- `/iclock/getrequest` — device command polling
- `/iclock/devicecmd` — command acknowledgements and responses
- `/iclock/ping` — device heartbeat
- `/iclock/fdata` — auxiliary firmware data

The routes deliberately return device-compatible `text/plain`, not JSON.

## Production deployment

1. Clone your repository and enter the project directory:

   ```bash
   cd /opt/
   git clone https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
   cd YOUR-REPOSITORY
   ```

2. Copy the example configuration:

   ```bash
   cp .env.example .env
   ```

3. Review `.env`. The supplied login is `admin` / `chetan@123`; replace `SESSION_SECRET` with a long random value:

   ```dotenv
   POSTGRES_PASSWORD=change-me
   DATABASE_URL=postgresql+asyncpg://smri:change-me@db:5432/smri_attendance
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=chetan@123
   SESSION_SECRET=replace-with-a-long-random-secret
   PUBLIC_BASE_URL=http://your-server-ip:8010
   TIMEZONE=Asia/Kolkata
   ```

4. Start the application:

   ```bash
   docker compose up -d --build
   docker compose ps
   curl http://127.0.0.1:8010/health
   ```

5. Allow TCP port `8010` in the server firewall/security group. Optionally put Caddy, Nginx, or a cloud load balancer in front of it for HTTPS.

6. Open `http://your-server-ip:8010/admin` and use the login page. The supplied defaults are `admin` / `chetan@123`.

7. On one test device, replace the old server host/IP and port with the new public host and port. Leave the protocol path at its default; the device requests `/iclock/...` itself.

Useful Docker commands:

```bash
# Follow application and database logs
docker compose logs -f

# Restart the application
docker compose restart app

# Pull later GitHub changes and rebuild
git pull
docker compose up -d --build

# Stop the stack without deleting PostgreSQL data
docker compose down
```

Watch the Devices and Punches pages before moving additional devices. Different firmware sometimes uses different names for optional biometric fields; raw inbound data and command responses are retained to diagnose those variations safely.

## Local development

SQLite is used when `DATABASE_URL` is not set:

```bash
uv sync --extra dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

The default login is `admin` / `chetan@123`.

Run tests with:

```bash
uv run pytest -q
```

## Webhook format

Each stored punch creates one delivery per enabled destination. Deliveries retry indefinitely with exponential backoff capped at one hour and do not hold up the device acknowledgement. A temporary customer-site or network outage therefore cannot discard a queued punch.

Destinations can apply to all devices or one selected device. Device-specific and central destinations may coexist; a matching punch is delivered to both. Two payload formats are available:

- `Normalized JSON` uses the JSON body documented below.
- `Frappe attendance function` mirrors the legacy sender with a JSON request containing `device_employee_id` and `data`. `data` is a JSON-string list, and `attendance_from_device` uses the ISO-compatible `YYYY-MM-DD HH:MM:SS` format in Asia/Kolkata time.

Each destination can optionally send `Authorization: Bearer <token>` and/or an `X-SMRI-Signature` HMAC header.

```json
{
  "event": "attendance.punch",
  "event_id": "485a2fc6-5334-477e-935d-a25285d718a4",
  "device_serial": "CN4C232160151",
  "employee_code": "42",
  "punch_time": "2026-10-26T04:01:20+00:00",
  "status": "0",
  "verification_type": "1",
  "work_code": null,
  "received_at": "2026-10-26T04:01:22+00:00"
}
```

When a destination has a signing secret, the request includes:

```text
X-SMRI-Signature: sha256=<hex HMAC-SHA256 of the exact request body>
X-SMRI-Event-ID: <event UUID>
```

Return any `2xx` response to acknowledge delivery. The same `event_id` can be used by receivers for their own idempotency.

## Backup

Create a database backup:

```bash
docker compose exec -T db pg_dump -U smri -d smri_attendance > smri-attendance-backup.sql
```

Restore into an empty database:

```bash
docker compose exec -T db psql -U smri -d smri_attendance < smri-attendance-backup.sql
```

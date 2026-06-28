# MMC Hostel ERP Backend

FastAPI backend starter for the MMC Hostel ERP frontend.

## Setup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Use the existing MySQL database:

```sql
CREATE DATABASE IF NOT EXISTS MMC_HOSTEL_ERP CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

Create `backend/.env` with your local database URL:

```text
DATABASE_URL=mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/MMC_HOSTEL_ERP
```

`@` must be written as `%40` inside the URL password.

Run migrations/schema:

```powershell
mysql -u root -p MMC_HOSTEL_ERP < mysql_schema.sql
```

Seed Mahima and Vaidehi hostels with their room inventory:

```powershell
mysql -u root -p MMC_HOSTEL_ERP < seed_hostels_rooms.sql
```

If the MySQL CLI is not available, use the Python seed runner:

```powershell
python seed_hostels_rooms.py
```

Remove non-admin data while preserving `admin_users`:

```powershell
mysql -u root -p MMC_HOSTEL_ERP < reset_non_admin_data.sql
```

Run the API:

```powershell
uvicorn app.main:app --reload
```

Open:

- API: `http://127.0.0.1:8000`
- Docs: `http://127.0.0.1:8000/docs`

Create the first real admin from the API docs or PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/admins -ContentType "application/json" -Body '{"username":"admin","email":"admin@magadhmahilacollege.ac.in","password":"ChangeThis@123","full_name":"Hostel Administrator","role":"super_admin"}'
```

Then use Admin Login on `student/login.html`. Student login and registration create real student IDs.

## Main endpoints

- `GET /health`
- `POST /auth/login`
- `POST /admins`
- `GET /admins`
- `POST /students/register`
- `POST /students`
- `GET /students`
- `GET /students/{student_id}`
- `POST /applications`
- `GET /applications`
- `GET /applications/{application_id}`
- `PATCH /applications/{application_id}/status`
- `GET /hostels`
- `POST /hostels`
- `GET /rooms`
- `POST /rooms`
- `POST /payments`
- `GET /payments`
- `GET /receipts`
- `POST /receipts/generate`
- `GET /receipts/{receipt_id}`
- `GET /receipts/{receipt_id}/download`
- `GET /receipts/verify/{receipt_number}`

## Railway deployment

Railway runs the API with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Use the MySQL service variables in the backend service. The app accepts `DATABASE_URL`, `MYSQL_URL`, or `MYSQL_PUBLIC_URL`; Railway's `mysql://...` values are converted automatically to SQLAlchemy's `mysql+pymysql://...` driver URL.

For the deployed backend service, prefer Railway's internal database URL:

```text
MYSQL_URL=mysql://root:<password>@mysql.railway.internal:3306/railway
```

For local development from your PC, use the public proxy URL instead:

```text
DATABASE_URL=mysql+pymysql://root:<password>@reseau.proxy.rlwy.net:35994/railway
```

The service health check is:

```text
/health
```

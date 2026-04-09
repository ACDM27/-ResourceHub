#!/usr/bin/env bash
set -euo pipefail

TOKEN="$(docker exec -i campus-admin-backend python - <<'PY'
from app.database import SessionLocal
from app import models
from jose import jwt

db = SessionLocal()
phone = db.query(models.User.phone).filter(models.User.role == "admin").first()[0]
print(jwt.encode({"sub": phone, "role": "admin"}, "mvp_secret_key_change_me", algorithm="HS256"))
PY
)"

echo "__DASHBOARD__"
curl -s -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8090/admin-api/admin/dashboard/stats
echo
echo "__SUNSHINE__"
curl -s -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:8090/admin-api/admin/sunshine/class-stats
echo

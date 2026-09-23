#!/usr/bin/env bash
# Backend repo path: scripts/bootstrap.sh
# Runs on the EC2 instance (first boot and on every deploy).
# Single-bucket version: code and config both live in s3general-148535751717-ap-east-1-an.
set -euxo pipefail

APP_ROOT="${APP_ROOT:-/opt/myTradingAgentsBackend}"
CODE_BUCKET="${CODE_BUCKET:-s3general-148535751717-ap-east-1-an}"
CONFIG_BUCKET="${CONFIG_BUCKET:-s3general-148535751717-ap-east-1-an}"
SERVICE_NAME="${SERVICE_NAME:-mytradingagents-backend}"
RELEASE_FILE="${RELEASE_FILE:-current/release.txt}"

if ! command -v unzip >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
      awscli curl git unzip python3 python3-pip python3-venv jq \
      build-essential libpq-dev
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y \
      aws-cli curl git unzip python3 python3-pip jq \
      gcc libpq-devel
  elif command -v yum >/dev/null 2>&1; then
    yum install -y \
      aws-cli curl git unzip python3 python3-pip jq \
      gcc libpq-devel
  else
    echo "No supported package manager found (apt-get, dnf, or yum)" >&2
    exit 1
  fi
fi

mkdir -p "${APP_ROOT}" "${APP_ROOT}/data" "${APP_ROOT}/artifacts"

aws s3 cp "s3://${CODE_BUCKET}/${RELEASE_FILE}" /tmp/current-release.txt
RELEASE_SHA="$(tr -d '\r\n' < /tmp/current-release.txt)"
aws s3 cp "s3://${CODE_BUCKET}/releases/${RELEASE_SHA}/backend.zip" /tmp/backend.zip || {
  echo "Missing release artifact for SHA ${RELEASE_SHA}" >&2
  exit 1
}

# clear previous code, keep data/, artifacts/ and .env
rm -rf "${APP_ROOT}/app"
rm -f "${APP_ROOT}/requirements.txt" "${APP_ROOT}/requirements-dev.txt" "${APP_ROOT}/mcp_server.py"

unzip -o /tmp/backend.zip -d "${APP_ROOT}"

# Validate the release bundle before starting the service
if [ ! -f "${APP_ROOT}/requirements.txt" ]; then
  echo "Deploy bundle is missing requirements.txt" >&2
  exit 1
fi
if [ ! -f "${APP_ROOT}/app/main.py" ]; then
  echo "Deploy bundle is missing app/main.py" >&2
  exit 1
fi

# .env is never in the repo: it comes from S3 config.
# A missing S3 .env is non-fatal so the service can at least start and serve
# /health (required for the deploy health-check to pass).  Without secrets the
# analysis endpoints will not work, but infrastructure/routing is unaffected.
if ! aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${APP_ROOT}/.env" 2>/dev/null; then
  echo "Warning: config/.env not found in S3 bucket ${CONFIG_BUCKET}." \
       "Using .env.example as a safe default; upload a real .env to S3 to enable all features." >&2
  if [ -f "${APP_ROOT}/.env.example" ]; then
    cp "${APP_ROOT}/.env.example" "${APP_ROOT}/.env"
  else
    touch "${APP_ROOT}/.env"
  fi
fi
chmod 600 "${APP_ROOT}/.env"

if [ ! -d "${APP_ROOT}/.venv" ]; then
  python3 -m venv "${APP_ROOT}/.venv"
fi
"${APP_ROOT}/.venv/bin/pip" install --upgrade pip setuptools wheel
"${APP_ROOT}/.venv/bin/pip" install --no-cache-dir -r "${APP_ROOT}/requirements.txt"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=myTradingAgents Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_ROOT}
Environment=PYTHONPATH=${APP_ROOT}
EnvironmentFile=${APP_ROOT}/.env
ExecStart=${APP_ROOT}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
KillMode=process

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

# ── Early diagnostics: surface the real error immediately ───
sleep 10
echo "=== systemctl status ==="
systemctl status "${SERVICE_NAME}.service" --no-pager || true
echo "=== journalctl (last 100 lines) ==="
journalctl -u "${SERVICE_NAME}.service" -n 100 --no-pager || true

for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    echo "Backend healthy on release ${RELEASE_SHA}."
    exit 0
  fi
  sleep 5
done

journalctl -u "${SERVICE_NAME}.service" -n 50 --no-pager >&2 || true
echo "Backend did not become healthy after bootstrap." >&2
exit 1

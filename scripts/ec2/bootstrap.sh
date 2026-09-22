#!/usr/bin/env bash
set -euxo pipefail

APP_ROOT="${APP_ROOT:-/opt/myTradingAgentsBackend}"
APP_NAME="${APP_NAME:-myTradingAgentsBackend}"
CODE_BUCKET="${CODE_BUCKET:-mytradingagents-code}"
CONFIG_BUCKET="${CONFIG_BUCKET:-mytradingagents-config}"
SERVICE_NAME="${SERVICE_NAME:-mytradingagents-backend}"
RELEASE_FILE="${RELEASE_FILE:-current/release.txt}"

export DEBIAN_FRONTEND=noninteractive

# Install base packages required by the Python service and AWS CLI.
apt-get update
apt-get install -y --no-install-recommends \
  awscli \
  unzip \
  python3 \
  python3-pip \
  python3-venv \
  jq

mkdir -p "${APP_ROOT}"

# Read the latest release SHA from S3 and download the code bundle.
aws s3 cp "s3://${CODE_BUCKET}/${RELEASE_FILE}" /tmp/current-release.txt
RELEASE_SHA="$(tr -d '\r\n' < /tmp/current-release.txt)"
aws s3 cp "s3://${CODE_BUCKET}/releases/${RELEASE_SHA}/backend.zip" /tmp/backend.zip

# Remove any stale app content before unpacking the new release.
rm -rf "${APP_ROOT}"/* "${APP_ROOT}"/.[!.]* "${APP_ROOT}"/..?* 2>/dev/null || true
unzip -o /tmp/backend.zip -d "${APP_ROOT}"

# Pull the runtime configuration from S3 instead of storing secrets in the AMI.
mkdir -p "${APP_ROOT}"
aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${APP_ROOT}/.env"

# Create a virtual environment and install Python dependencies.
python3 -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/pip" install --upgrade pip
"${APP_ROOT}/.venv/bin/pip" install -r "${APP_ROOT}/requirements.txt"

# Configure a systemd service so the app starts automatically on boot and restarts if it crashes.
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=${APP_NAME} API
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

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

# Wait for the app to come up and ensure the health endpoint responds.
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 5
done

echo "Application did not become healthy after bootstrap." >&2
exit 1

#!/usr/bin/env bash
# Backend repo path: scripts/bootstrap.sh
# Runs on the EC2 instance (first boot and on every deploy).
# Single-bucket version: code and config both live in the same S3 bucket.
set -euxo pipefail

APP_ROOT="${APP_ROOT:-/opt/myTradingAgentsBackend}"
CODE_BUCKET="${CODE_BUCKET:-s3general-148535751717-ap-east-1-an}"
CONFIG_BUCKET="${CONFIG_BUCKET:-s3general-148535751717-ap-east-1-an}"
SERVICE_NAME="${SERVICE_NAME:-mytradingagents-backend}"
RELEASE_FILE="${RELEASE_FILE:-current/release.txt}"
SVC_USER="${SVC_USER:-mytradingagents}"

# ── Detect package manager (Amazon Linux 2023 = dnf, Ubuntu = apt-get) ──
if command -v dnf >/dev/null 2>&1; then
  PKG_MGR="dnf"
elif command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  PKG_MGR="apt-get"
else
  echo "No supported package manager found (dnf or apt-get)" >&2
  exit 1
fi

# ── System packages ─────────────────────────────────────────────
# Check every critical tool, not just unzip/python3.
NEED_INSTALL=false
for cmd in unzip python3.12 pip3 jq git gcc; do
  command -v "$cmd" >/dev/null 2>&1 || { NEED_INSTALL=true; break; }
done

if [ "$NEED_INSTALL" = true ]; then
  if [ "${PKG_MGR}" = "dnf" ]; then
    # AL2023 ships AWS CLI v2 pre-installed; do NOT add 'awscli2'.
    dnf install -y --allowerasing \
      curl unzip python3.12 python3-pip jq \
      gcc gcc-c++ make libpq-devel git
  else
    apt-get update
    apt-get install -y --no-install-recommends \
      awscli curl unzip python3.12 python3-pip python3.12-venv jq \
      build-essential libpq-dev git
  fi
fi

# Ensure Python 3.12 and AWS CLI are available regardless of install path
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Python 3.12 not found after package install" >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "AWS CLI not found after package install" >&2
  exit 1
fi

# ── Service user (do not run the app as root) ───────────────────
if ! id "${SVC_USER}" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${SVC_USER}"
fi

# ── Nginx reverse proxy (port 80 → uvicorn 8000) ───────────────
if ! command -v nginx >/dev/null 2>&1; then
  if [ "${PKG_MGR}" = "dnf" ]; then
    dnf install -y nginx
  else
    apt-get install -y --no-install-recommends nginx
  fi
fi

cat > /etc/nginx/conf.d/mytradingagents.conf <<'NGINX_EOF'
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Longer timeouts for analysis runs
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
NGINX_EOF

# Remove the default server block on BOTH distros
# AL2023 / RHEL-family
if [ -f /etc/nginx/conf.d/default.conf ]; then
  mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.disabled
fi
# Ubuntu / Debian-family
if [ -L /etc/nginx/sites-enabled/default ]; then
  rm -f /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl enable nginx
systemctl restart nginx

# ── Stop the running service BEFORE replacing code ──────────────
if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
  echo "Stopping ${SERVICE_NAME} before code deploy…"
  systemctl stop "${SERVICE_NAME}.service"
fi

# ── Deploy application code ─────────────────────────────────────
mkdir -p "${APP_ROOT}" "${APP_ROOT}/data" "${APP_ROOT}/artifacts"

aws s3 cp "s3://${CODE_BUCKET}/${RELEASE_FILE}" /tmp/current-release.txt
RELEASE_SHA="$(tr -d '\r\n' < /tmp/current-release.txt)"

aws s3 cp "s3://${CODE_BUCKET}/releases/${RELEASE_SHA}/backend.zip" /tmp/backend.zip || {
  echo "Missing release artifact for SHA ${RELEASE_SHA}" >&2
  exit 1
}

# Clear previous code, keep data/, artifacts/ and .env
rm -rf "${APP_ROOT}/app"
rm -f "${APP_ROOT}/requirements.txt" "${APP_ROOT}/requirements-dev.txt" "${APP_ROOT}/mcp_server.py"

unzip -o /tmp/backend.zip -d "${APP_ROOT}"

# Validate the release bundle
if [ ! -f "${APP_ROOT}/requirements.txt" ]; then
  echo "Deploy bundle is missing requirements.txt" >&2
  exit 1
fi
if [ ! -f "${APP_ROOT}/app/main.py" ]; then
  echo "Deploy bundle is missing app/main.py" >&2
  exit 1
fi

# ── Pull .env from S3 (never stored in the repo) ───────────────
# NOTE: systemd EnvironmentFile does NOT support 'export' prefixes or
#       shell-style quoting.  Every line must be plain KEY=VALUE.
aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${APP_ROOT}/.env" || {
  echo "Missing .env in S3 config bucket" >&2
  exit 1
}
chmod 600 "${APP_ROOT}/.env"

# ── Python virtual environment and dependencies ─────────────────
# Recreate the venv if it was created with Python older than 3.12.
if [ -x "${APP_ROOT}/.venv/bin/python3" ]; then
  VENV_MAJOR="$("${APP_ROOT}/.venv/bin/python3" -c \
    'import sys; print(sys.version_info.major)' 2>/dev/null || true)"
  VENV_MINOR="$("${APP_ROOT}/.venv/bin/python3" -c \
    'import sys; print(sys.version_info.minor)' 2>/dev/null || true)"

  if [ -n "${VENV_MAJOR}" ] && [ -n "${VENV_MINOR}" ] && \
     { [ "${VENV_MAJOR}" -lt 3 ] || { [ "${VENV_MAJOR}" -eq 3 ] && [ "${VENV_MINOR}" -lt 12 ]; }; }; then
    echo "Existing venv uses Python ${VENV_MAJOR}.${VENV_MINOR} (< 3.12); recreating venv…"
    rm -rf "${APP_ROOT}/.venv"
  fi
fi

if [ ! -x "${APP_ROOT}/.venv/bin/python3" ]; then
  python3.12 -m venv "${APP_ROOT}/.venv"
fi

"${APP_ROOT}/.venv/bin/pip" install --upgrade pip setuptools wheel
"${APP_ROOT}/.venv/bin/pip" install --no-cache-dir -r "${APP_ROOT}/requirements.txt"

# ── Fix ownership ───────────────────────────────────────────────
chown -R "${SVC_USER}:${SVC_USER}" "${APP_ROOT}"

# ── Systemd service ─────────────────────────────────────────────
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=myTradingAgents Backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${APP_ROOT}
Environment=PYTHONPATH=${APP_ROOT}
EnvironmentFile=${APP_ROOT}/.env
ExecStart=${APP_ROOT}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=30
KillMode=mixed

# Hardening
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=${APP_ROOT}/data ${APP_ROOT}/artifacts

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

# ── Cleanup temp files ──────────────────────────────────────────
rm -f /tmp/backend.zip /tmp/current-release.txt

# ── Early diagnostics ───────────────────────────────────────────
sleep 10
echo "=== systemctl status (backend) ==="
systemctl status "${SERVICE_NAME}.service" --no-pager || true
echo "=== journalctl (last 100 lines) ==="
journalctl -u "${SERVICE_NAME}.service" -n 100 --no-pager || true
echo "=== nginx status ==="
systemctl status nginx --no-pager || true

# ── Health check loop (up to 150 seconds) ───────────────────────
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
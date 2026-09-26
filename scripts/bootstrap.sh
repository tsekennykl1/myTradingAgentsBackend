#!/usr/bin/env bash
# Backend repo path: scripts/bootstrap.sh
# Runs on the EC2 instance (first boot and on every deploy, via SSM).
#
# Consolidated version — keeps the PM2 process manager, nginx reverse proxy,
# CloudWatch Agent and SSM Agent setup, and adds the fixes from the manual
# debugging sessions:
#   • .env ownership/permissions fixed every deploy (the service user must be
#     able to read it — fixes "Permission denied: '.env'").
#   • TRADINGAGENTS_* model names auto-corrected to the litellm prefixed
#     format (deepseek/deepseek-reasoner, deepseek/deepseek-chat) — fixes
#     "LLM Provider NOT provided" errors from unprefixed deepseek-v4-* names.
#   • SSM Agent verified running so deploy.yml's SendCommand never stalls.
#   • Legacy systemd unit (mytradingagents-backend.service) removed on the
#     first PM2 deploy so it can never fight PM2 for port 8000.
# Idempotent — safe to re-run.
set -euxo pipefail

# ── Configuration ─────────────────────────────────────────────
APP_ROOT="${APP_ROOT:-/opt/myTradingAgentsBackend}"
CODE_BUCKET="${CODE_BUCKET:-s3general-148535751717-ap-east-1-an}"
CONFIG_BUCKET="${CONFIG_BUCKET:-s3general-148535751717-ap-east-1-an}"
SERVICE_NAME="${SERVICE_NAME:-mytradingagents-backend}"        # legacy name
PM2_APP_NAME="${PM2_APP_NAME:-mytradingagents-backend}"        # PM2 process name
RELEASE_FILE="${RELEASE_FILE:-current/release.txt}"
SVC_USER="${SVC_USER:-mytradingagents}"

# ── Detect package manager (AL2023 = dnf, Ubuntu = apt-get) ──
if command -v dnf >/dev/null 2>&1; then
  PKG_MGR="dnf"
elif command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  PKG_MGR="apt-get"
else
  echo "No supported package manager found (dnf or apt-get)" >&2
  exit 1
fi

# ── System packages ──────────────────────────────────────────
NEED_INSTALL=false
for cmd in unzip python3.12 pip3.12 jq git gcc curl; do
  command -v "$cmd" >/dev/null 2>&1 || { NEED_INSTALL=true; break; }
done

if [ "$NEED_INSTALL" = true ]; then
  if [ "${PKG_MGR}" = "dnf" ]; then
    # AL2023 ships AWS CLI v2 pre-installed; do NOT add 'awscli2'.
    dnf install -y --allowerasing \
      curl unzip python3.12 python3.12-pip jq \
      gcc gcc-c++ make libpq-devel git tar
  else
    apt-get update
    apt-get install -y --no-install-recommends \
      awscli curl unzip python3.12 python3-pip python3.12-venv jq \
      build-essential libpq-dev git
  fi
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "AWS CLI not found after package install" >&2
  exit 1
fi

# ── SSM Agent — must be running or deploy.yml's SendCommand stalls ──
# AL2023 ships it pre-installed; Ubuntu may need the snap. Non-fatal.
if ! systemctl is-active --quiet amazon-ssm-agent 2>/dev/null; then
  echo "SSM Agent not active — installing/starting…"
  if [ "${PKG_MGR}" = "dnf" ]; then
    dnf install -y amazon-ssm-agent 2>/dev/null || true
  else
    snap install amazon-ssm-agent --classic 2>/dev/null || true
  fi
  systemctl enable amazon-ssm-agent 2>/dev/null || true
  systemctl start  amazon-ssm-agent 2>/dev/null || true
fi
systemctl is-active --quiet amazon-ssm-agent \
  && echo "SSM Agent is running ✓" \
  || echo "WARNING: SSM Agent not running — SSM deploys will not reach this instance" >&2

# ── Node.js & PM2 ─────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
  echo "Installing Node.js…"
  if [ "${PKG_MGR}" = "dnf" ]; then
    # AL2023 ships Node 18+ in its repos — sufficient for PM2.
    dnf install -y nodejs npm
  else
    # Ubuntu: pull Node 20.x LTS from NodeSource for a recent version.
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
  fi
fi

echo "Node.js $(node --version)  npm $(npm --version)"

if ! command -v pm2 >/dev/null 2>&1; then
  npm install -g pm2
fi

PM2_BIN="$(command -v pm2)"
echo "PM2 $("${PM2_BIN}" --version) at ${PM2_BIN}"

# ── CloudWatch Agent — RAM + disk monitoring ──────────────────
# Installs the agent on first boot, writes a fresh config on every deploy,
# and (re)starts the daemon.  Failures here are non-fatal — monitoring
# should never block a deploy.
install_cloudwatch_agent() {
  echo "Setting up CloudWatch Agent…"

  # 1. Install if not already present
  if ! command -v amazon-cloudwatch-agent-ctl >/dev/null 2>&1 \
     && [ ! -x /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl ]; then
    if [ "${PKG_MGR}" = "dnf" ]; then
      dnf install -y amazon-cloudwatch-agent
    else
      apt-get install -y --no-install-recommends amazon-cloudwatch-agent
    fi
  fi

  CW_CTL="/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
  CW_CFG="/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json"

  # 2. Write / overwrite config (idempotent)
  cat > "${CW_CFG}" <<'CW_EOF'
{
  "metrics": {
    "namespace": "CWAgent",
    "metrics_collected": {
      "mem": {
        "measurement": ["mem_used_percent"],
        "metrics_collection_interval": 60
      },
      "disk": {
        "measurement": ["used_percent"],
        "metrics_collection_interval": 60,
        "resources": ["/"]
      }
    }
  }
}
CW_EOF

  # 3. (Re)start the agent with the new config
  "${CW_CTL}" -a fetch-config -m ec2 -c "file:${CW_CFG}" -s

  # 4. Verify
  if systemctl is-active --quiet amazon-cloudwatch-agent; then
    echo "CloudWatch Agent is running ✓"
  else
    echo "WARNING: CloudWatch Agent failed to start (non-fatal)" >&2
  fi
}

install_cloudwatch_agent || echo "WARNING: CloudWatch Agent setup failed — continuing deploy" >&2

# ── Service user needs a real home dir (PM2 keeps state in ~/.pm2/) ──
if ! id "${SVC_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/home/${SVC_USER}" \
          --shell /bin/bash "${SVC_USER}"
elif [ ! -d "/home/${SVC_USER}" ]; then
  # User already exists from the old systemd bootstrap but had no home dir.
  mkdir -p "/home/${SVC_USER}"
  chown "${SVC_USER}:${SVC_USER}" "/home/${SVC_USER}"
  usermod --home "/home/${SVC_USER}" --shell /bin/bash "${SVC_USER}"
fi
SVC_HOME="/home/${SVC_USER}"

# ── Migrate: remove legacy systemd unit ───────────────────────
# On the first PM2 deploy, the old raw-uvicorn unit may still be present.
# Stop it, disable it, and delete the file so it never fights PM2 for
# port 8000.
if [ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]; then
  echo "Removing legacy systemd unit ${SERVICE_NAME}.service…"
  systemctl stop    "${SERVICE_NAME}.service" 2>/dev/null || true
  systemctl disable "${SERVICE_NAME}.service" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
fi
# Also kill any manually started uvicorn (e.g. from a debugging session),
# otherwise it holds port 8000 and the PM2 process fails to bind.
pkill -f "uvicorn app.main:app" 2>/dev/null || true

# ── Nginx reverse proxy (port 80 → uvicorn 8000) ─────────────
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
if [ -f /etc/nginx/conf.d/default.conf ]; then
  mv /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.disabled
fi
if [ -L /etc/nginx/sites-enabled/default ]; then
  rm -f /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl enable nginx
systemctl restart nginx

# ── Stop running PM2 app BEFORE replacing code ────────────────
sudo -u "${SVC_USER}" "${PM2_BIN}" delete "${PM2_APP_NAME}" 2>/dev/null || true

# ── Deploy application code ──────────────────────────────────
mkdir -p "${APP_ROOT}" "${APP_ROOT}/data" "${APP_ROOT}/artifacts" "${APP_ROOT}/logs"

aws s3 cp "s3://${CODE_BUCKET}/${RELEASE_FILE}" /tmp/current-release.txt
RELEASE_SHA="$(tr -d '\r\n' < /tmp/current-release.txt)"

aws s3 cp "s3://${CODE_BUCKET}/releases/${RELEASE_SHA}/backend.zip" /tmp/backend.zip || {
  echo "Missing release artifact for SHA ${RELEASE_SHA}" >&2
  exit 1
}

# Clear previous code but keep data/, artifacts/, logs/ and .env
rm -rf "${APP_ROOT}/app"
rm -f "${APP_ROOT}/requirements.txt" "${APP_ROOT}/requirements-dev.txt" \
      "${APP_ROOT}/mcp_server.py"

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

# ── Pull .env from S3 (never stored in the repo) ─────────────
# NOTE: PM2 does NOT support systemd's EnvironmentFile=.  The wrapper
# script start-backend.sh below sources this file before exec-ing uvicorn.
ENV_PATH="${APP_ROOT}/.env"
TMP_ENV_PATH="/tmp/deploy-env.$$"
if aws s3 cp "s3://${CONFIG_BUCKET}/config/.env" "${TMP_ENV_PATH}"; then
  mv "${TMP_ENV_PATH}" "${ENV_PATH}"
  echo "Loaded .env from s3://${CONFIG_BUCKET}/config/.env"
elif [ -f "${ENV_PATH}" ]; then
  rm -f "${TMP_ENV_PATH}"
  echo "WARNING: Missing .env in S3 config bucket; using existing ${ENV_PATH}" >&2
else
  rm -f "${TMP_ENV_PATH}"
  cat > "${ENV_PATH}" <<'ENV_EOF'
# Auto-generated by deploy bootstrap because no runtime .env was available.
MCP_ENABLED=0
ENV_EOF
  echo "WARNING: Missing .env in S3 config bucket and no existing ${ENV_PATH}; wrote minimal runtime defaults with MCP disabled." >&2
  echo "WARNING: Provider-backed analysis stays unavailable until a real .env is uploaded." >&2
fi

# ── .env auto-repairs (idempotent) ────────────────────────────
# 1. Model names: litellm needs the provider prefix (deepseek/...).
#    Only rewrites the known-broken unprefixed values; correct lines untouched.
sed -i \
  -e 's|^TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-pro|TRADINGAGENTS_DEEP_THINK_LLM=deepseek/deepseek-reasoner|' \
  -e 's|^TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash|TRADINGAGENTS_QUICK_THINK_LLM=deepseek/deepseek-chat|' \
  "${ENV_PATH}"

# 2. Ownership/permissions: the service user must be able to READ .env.
#    Fixes "Permission denied: '.env'" when the file was created with sudo/root.
chown "${SVC_USER}:${SVC_USER}" "${ENV_PATH}"
chmod 600 "${ENV_PATH}"

# ── Python virtual environment and dependencies ──────────────
# tradingagents requires Python >=3.12; always use python3.12 explicitly.
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 not found after package install" >&2
  exit 1
fi

VENV_MAJOR=""
VENV_MINOR=""
if [ -x "${APP_ROOT}/.venv/bin/python3" ]; then
  VENV_MAJOR="$("${APP_ROOT}/.venv/bin/python3" -c \
    'import sys; print(sys.version_info.major)' 2>/dev/null || true)"
  VENV_MINOR="$("${APP_ROOT}/.venv/bin/python3" -c \
    'import sys; print(sys.version_info.minor)' 2>/dev/null || true)"

  if [ -n "${VENV_MAJOR}" ] && [ -n "${VENV_MINOR}" ] && \
     { [ "${VENV_MAJOR}" -lt 3 ] || { [ "${VENV_MAJOR}" = "3" ] && [ "${VENV_MINOR}" -lt 12 ]; }; }; then
    echo "Existing venv uses Python ${VENV_MAJOR}.${VENV_MINOR} (< 3.12); recreating venv…"
    rm -rf "${APP_ROOT}/.venv"
  fi
fi

if [ ! -x "${APP_ROOT}/.venv/bin/python3" ]; then
  python3.12 -m venv "${APP_ROOT}/.venv" || {
    echo "Failed to create Python virtual environment (python3.12)" >&2
    exit 1
  }
fi

"${APP_ROOT}/.venv/bin/pip" install --upgrade pip setuptools wheel
"${APP_ROOT}/.venv/bin/pip" install --no-cache-dir -r "${APP_ROOT}/requirements.txt"

# ── Start wrapper script ──────────────────────────────────────
# This replaces systemd's EnvironmentFile= + ExecStart=.
# `set -a` exports every variable sourced from .env so uvicorn sees them.
cat > "${APP_ROOT}/start-backend.sh" <<'WRAPPER_EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

# Load runtime secrets / config from .env
set -a
source .env
set +a
export PYTHONPATH="${SCRIPT_DIR}"

exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
WRAPPER_EOF
chmod +x "${APP_ROOT}/start-backend.sh"

# ── Ecosystem config ──────────────────────────────────────────
cat > "${APP_ROOT}/ecosystem.config.cjs" <<EOF
// Auto-generated by bootstrap.sh — edits will be overwritten on next deploy.
module.exports = {
  apps: [{
    name:               '${PM2_APP_NAME}',
    script:             './start-backend.sh',
    cwd:                '${APP_ROOT}',
    interpreter:        '/bin/bash',
    instances:          1,
    autorestart:        true,
    watch:              false,
    max_memory_restart: '1G',
    restart_delay:      5000,
    max_restarts:       15,
    log_date_format:    'YYYY-MM-DD HH:mm:ss Z',
    merge_logs:         true,
    out_file:           '${APP_ROOT}/logs/backend-out.log',
    error_file:         '${APP_ROOT}/logs/backend-error.log',
  }]
};
EOF

# ── Fix ownership (whole app dir belongs to the service user) ──
chown -R "${SVC_USER}:${SVC_USER}" "${APP_ROOT}"

# ── Launch via PM2 ────────────────────────────────────────────
sudo -u "${SVC_USER}" bash -c \
  "cd ${APP_ROOT} && ${PM2_BIN} start ecosystem.config.cjs"

# ── Boot persistence (pm2 startup + save) ─────────────────────
# This creates /etc/systemd/system/pm2-${SVC_USER}.service which runs
# `pm2 resurrect` on boot, bringing back every saved process.
env PATH="$PATH" "${PM2_BIN}" startup systemd \
  -u "${SVC_USER}" --hp "${SVC_HOME}" \
  --service-name "pm2-${SVC_USER}"
sudo -u "${SVC_USER}" "${PM2_BIN}" save

# ── Log rotation module ───────────────────────────────────────
sudo -u "${SVC_USER}" "${PM2_BIN}" install pm2-logrotate 2>/dev/null || true
sudo -u "${SVC_USER}" "${PM2_BIN}" set pm2-logrotate:max_size  10M  2>/dev/null || true
sudo -u "${SVC_USER}" "${PM2_BIN}" set pm2-logrotate:retain    10   2>/dev/null || true
sudo -u "${SVC_USER}" "${PM2_BIN}" set pm2-logrotate:compress  true 2>/dev/null || true

# ── Cleanup temp files ───────────────────────────────────────
rm -f /tmp/backend.zip /tmp/current-release.txt

# ── Diagnostics ───────────────────────────────────────────────
sleep 8
echo "=== PM2 process list ==="
sudo -u "${SVC_USER}" "${PM2_BIN}" list
echo "=== PM2 logs (last 60 lines) ==="
sudo -u "${SVC_USER}" "${PM2_BIN}" logs --nostream --lines 60 || true
echo "=== nginx status ==="
systemctl status nginx --no-pager || true
echo "=== CloudWatch Agent status ==="
systemctl status amazon-cloudwatch-agent --no-pager || true
echo "=== SSM Agent status ==="
systemctl status amazon-ssm-agent --no-pager || true

# ── Health check loop (up to 150 seconds) ────────────────────
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    echo "Backend healthy on release ${RELEASE_SHA}."
    exit 0
  fi
  sleep 5
done

# Dump more logs on failure to help debug
echo "=== PM2 logs on failure (last 100 lines) ==="
sudo -u "${SVC_USER}" "${PM2_BIN}" logs --nostream --lines 100 || true
echo "Backend did not become healthy after bootstrap." >&2
exit 1

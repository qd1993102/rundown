#!/usr/bin/env bash

set -Eeuo pipefail

WORK_DIR="${WORK_DIR:-/opt/neurun-deploy}"
SOURCE_DIR="${SOURCE_DIR:-${WORK_DIR}/code_deploy_application}"
RELEASES_DIR="${RELEASES_DIR:-/opt/neurun-releases}"
CURRENT_LINK="${CURRENT_LINK:-/opt/neurun-current}"
DATA_DIR="${DATA_DIR:-/var/lib/neurun}"
ENV_DIR="${ENV_DIR:-/etc/neurun}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/neurun.service}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
RUN_USER="${RUN_USER:-neurun}"
RUN_GROUP="${RUN_GROUP:-neurun}"

# 下载暂存区必须在任何安装、软链接切换或 systemd 操作前通过检查。
if [ ! -f "${SOURCE_DIR}/pyproject.toml" ]; then
  echo "错误：Git 仓库未成功下载，未找到 ${SOURCE_DIR}/pyproject.toml；当前应用保持运行" >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3.12 || command -v python3 || command -v python || true)"

if [ -z "${PYTHON_BIN}" ]; then
  echo "错误：未找到 Python 可执行文件；当前应用保持运行" >&2
  exit 1
fi

if ! "${PYTHON_BIN}" -c \
  'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'
then
  echo "错误：需要 Python 3.12+，当前版本为：$("${PYTHON_BIN}" --version 2>&1)；当前应用保持运行" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "错误：未找到 curl，无法执行发布后健康检查；当前应用保持运行" >&2
  exit 1
fi

echo "使用 Python：${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"

if ! id "${RUN_USER}" >/dev/null 2>&1; then
  useradd --system \
    --user-group \
    --home-dir "${DATA_DIR}" \
    --shell /sbin/nologin \
    "${RUN_USER}"
fi

install -d -m 0755 "${RELEASES_DIR}"
install -d -m 0700 -o "${RUN_USER}" -g "${RUN_GROUP}" "${DATA_DIR}"
install -d -m 0750 "${ENV_DIR}"

RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
install -d -m 0755 "${RELEASE_DIR}"

# 只复制到新 release；不覆盖当前版本。
cp -a "${SOURCE_DIR}/." "${RELEASE_DIR}/"

if [ ! -f "${RELEASE_DIR}/pyproject.toml" ]; then
  echo "错误：新 release 复制不完整；当前应用保持运行" >&2
  exit 1
fi

"${PYTHON_BIN}" -m venv --clear "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${RELEASE_DIR}/.venv/bin/python" -m pip install -e "${RELEASE_DIR}"

# 在切换和重启前先确认服务入口可导入。
(
  cd "${RELEASE_DIR}"
  "${RELEASE_DIR}/.venv/bin/python" -c 'from src.main import cmd_serve'
)

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=neurun Web Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${CURRENT_LINK}

Environment=PYTHONUNBUFFERED=1
Environment=NEURUN_DATA_DIR=${DATA_DIR}
Environment=NEURUN_INVITE_CODES_FILE=${DATA_DIR}/invite-codes.json
Environment=MCP_HOST=0.0.0.0
Environment=MCP_PORT=8080
Environment=MCP_TRANSPORT=sse
EnvironmentFile=-${ENV_DIR}/neurun.env

ExecStart=${CURRENT_LINK}/.venv/bin/python -c "from src.main import cmd_serve; cmd_serve()"

Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

OLD_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"

activate_release() {
  local target="$1"
  local next_link="${CURRENT_LINK}.next.$$"

  ln -s "${target}" "${next_link}"
  mv -Tf "${next_link}" "${CURRENT_LINK}"
}

wait_for_health() {
  local attempt

  for attempt in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restore_previous_release() {
  if [ -z "${OLD_RELEASE}" ] || [ ! -d "${OLD_RELEASE}" ]; then
    echo "错误：没有可回滚的旧 release" >&2
    return 1
  fi

  echo "新版本启动失败，正在恢复旧版本：${OLD_RELEASE}" >&2
  activate_release "${OLD_RELEASE}"
  "${SYSTEMCTL_BIN}" daemon-reload
  "${SYSTEMCTL_BIN}" restart neurun.service

  if ! wait_for_health; then
    echo "错误：旧版本已恢复，但健康检查仍未通过" >&2
    return 1
  fi

  echo "已恢复旧版本，服务继续运行：${OLD_RELEASE}" >&2
}

# 到这里新 release 已完成构建和导入检查，才切换在线指针。
activate_release "${RELEASE_DIR}"

if ! "${SYSTEMCTL_BIN}" daemon-reload \
  || ! "${SYSTEMCTL_BIN}" enable neurun.service >/dev/null \
  || ! "${SYSTEMCTL_BIN}" restart neurun.service
then
  echo "错误：systemd 未能启动新版本" >&2
  restore_previous_release || true
  exit 1
fi

if wait_for_health; then
  echo "neurun 启动成功，release=${RELEASE_ID}"
  "${SYSTEMCTL_BIN}" --no-pager --full status neurun.service || true
  exit 0
fi

echo "错误：neurun 新版本启动后健康检查未通过" >&2
journalctl -u neurun.service -n 100 --no-pager || true
restore_previous_release || true
exit 1

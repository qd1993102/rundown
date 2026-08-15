#!/usr/bin/env bash

set -Eeuo pipefail

WORK_DIR="${WORK_DIR:-/opt/neurun-deploy}"
SOURCE_DIR="${SOURCE_DIR:-}"
RELEASES_DIR="${RELEASES_DIR:-/opt/neurun-releases}"
CURRENT_LINK="${CURRENT_LINK:-/opt/neurun-current}"
DATA_DIR="${DATA_DIR:-/var/lib/neurun}"
ENV_DIR="${ENV_DIR:-/etc/neurun}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/neurun.service}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
RUN_USER="${RUN_USER:-neurun}"
RUN_GROUP="${RUN_GROUP:-neurun}"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-${WORK_DIR}/neurun-deploy.lock}"
CURRENT_SWITCHED=0

report_candidate_failure() {
  local status="$1"
  local line="$2"

  if [ "${CURRENT_SWITCHED}" -eq 0 ]; then
    echo "错误：候选版本在第 ${line} 行失败；当前 release、软链接和在线服务均未修改" >&2
  fi
  exit "${status}"
}

trap 'report_candidate_failure "$?" "$LINENO"' ERR

# 下载暂存区必须在任何安装、软链接切换或 systemd 操作前通过检查。
if [ -z "${SOURCE_DIR}" ]; then
  echo "错误：必须显式传入平台下载目录 SOURCE_DIR；当前应用保持运行" >&2
  exit 1
fi

if [ ! -f "${SOURCE_DIR}/pyproject.toml" ]; then
  echo "错误：Git 仓库未成功下载，未找到 ${SOURCE_DIR}/pyproject.toml；当前应用保持运行" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1 \
  || ! git -C "${SOURCE_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1
then
  echo "错误：SOURCE_DIR 不是可验证的 Git 工作树；拒绝发布" >&2
  exit 1
fi

SOURCE_COMMIT="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ ! "${SOURCE_COMMIT}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "错误：平台 checkout HEAD 不是 40 位小写 Git SHA；当前应用保持运行" >&2
  exit 1
fi

if [ -n "$(git -C "${SOURCE_DIR}" status --porcelain)" ]; then
  echo "错误：SOURCE_DIR 存在未提交或未跟踪文件；拒绝把脏工作树发布到线上" >&2
  exit 1
fi

if ! command -v flock >/dev/null 2>&1; then
  echo "错误：未找到 flock，无法阻止并发发布；当前应用保持运行" >&2
  exit 1
fi

exec 9>"${DEPLOY_LOCK_FILE}"
if ! flock -n 9; then
  echo "错误：已有 neurun 发布任务正在执行；当前应用保持运行" >&2
  exit 1
fi

echo "候选提交已确认：${SOURCE_COMMIT}"

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
# 部署环境文件目录属组改为运行组：systemd（root）与 neurun 进程（Web/CLI，
# 均以 User=neurun Group=neurun 运行）都要能遍历读取；组内可读（0750），
# 避免向其他本地用户暴露。仅在 root 下执行（部署脚本通常以 root 运行）；
# 非 root（本地测试）跳过属主修改。
if [ "$(id -u)" = "0" ]; then
  chown root:"${RUN_GROUP}" "${ENV_DIR}" 2>/dev/null || true
fi

# CLI 与 Web 共享同一配置源：确保部署环境文件包含数据目录与邀请码路径。
# 这样 `neurun invite create` 等管理员命令无需手工传 env，即与 Web
# （systemd EnvironmentFile 同源）读写同一文件，发布切换后位置不变。
if [ ! -f "${ENV_DIR}/neurun.env" ]; then
  umask 077
  : > "${ENV_DIR}/neurun.env"
fi
# 老部署可能留下 root:root 属主（目录 root:root 0750 时 neurun 用户连 stat
# 都会 EACCES），改为 root:RUN_GROUP 保证运行用户可读；文件组内只读 0640。
if [ "$(id -u)" = "0" ]; then
  chown root:"${RUN_GROUP}" "${ENV_DIR}/neurun.env" 2>/dev/null || true
fi
chmod 0640 "${ENV_DIR}/neurun.env" 2>/dev/null || true
if ! grep -q '^NEURUN_DATA_DIR=' "${ENV_DIR}/neurun.env"; then
  echo "NEURUN_DATA_DIR=${DATA_DIR}" >> "${ENV_DIR}/neurun.env"
fi
if ! grep -q '^NEURUN_INVITE_CODES_FILE=' "${ENV_DIR}/neurun.env"; then
  echo "NEURUN_INVITE_CODES_FILE=${DATA_DIR}/invite-codes.json" >> "${ENV_DIR}/neurun.env"
fi

if [ ! -f "${ENV_DIR}/neurun.env" ] \
  || ! grep -q '^NEURUN_COROS_CREDENTIAL_KEY=.' "${ENV_DIR}/neurun.env"
then
  echo "警告：未配置 NEURUN_COROS_CREDENTIAL_KEY；Coros 自动鉴权不会默认开启" >&2
fi

RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-${SOURCE_COMMIT:0:12}-$$"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
install -d -m 0755 "${RELEASE_DIR}"

# 只复制到新 release；不覆盖当前版本。
cp -a "${SOURCE_DIR}/." "${RELEASE_DIR}/"

if [ ! -f "${RELEASE_DIR}/pyproject.toml" ]; then
  echo "错误：新 release 复制不完整；当前应用保持运行" >&2
  exit 1
fi

printf '%s\n' "${SOURCE_COMMIT}" > "${RELEASE_DIR}/.neurun-release"

"${PYTHON_BIN}" -m venv --clear "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${RELEASE_DIR}/.venv/bin/python" -m pip install -e "${RELEASE_DIR}"

# 在切换和重启前先确认服务入口可导入。
(
  cd "${RELEASE_DIR}"
  "${RELEASE_DIR}/.venv/bin/python" -c 'from src.main import cmd_serve'
)

write_service_file() {
  local release_sha="$1"

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
Environment=NEURUN_RELEASE_SHA=${release_sha}

ExecStart=${CURRENT_LINK}/.venv/bin/python -c "from src.main import cmd_serve; cmd_serve()"

Restart=on-failure
RestartSec=5
TimeoutStopSec=30
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
EOF
}

write_service_file "${SOURCE_COMMIT}"

OLD_RELEASE="$(readlink -f "${CURRENT_LINK}" 2>/dev/null || true)"
OLD_RELEASE_COMMIT=""
if [ -n "${OLD_RELEASE}" ] && [ -f "${OLD_RELEASE}/.neurun-release" ]; then
  OLD_RELEASE_COMMIT="$(tr -d '\r\n' < "${OLD_RELEASE}/.neurun-release")"
fi

activate_release() {
  local target="$1"
  local next_link="${CURRENT_LINK}.next.$$"

  ln -s "${target}" "${next_link}"
  mv -Tf "${next_link}" "${CURRENT_LINK}"
}

wait_for_health() {
  local expected_release="${1:-}"
  local attempt
  local response
  local actual_release

  for attempt in $(seq 1 20); do
    response="$(curl -fsS http://127.0.0.1:8080/healthz 2>/dev/null || true)"
    if [ -z "${response}" ]; then
      sleep 1
      continue
    fi
    if [ -z "${expected_release}" ]; then
      return 0
    fi
    actual_release="$(printf '%s' "${response}" | "${RELEASE_DIR}/.venv/bin/python" -c \
      'import json, sys; print(json.load(sys.stdin).get("release", ""))' 2>/dev/null || true)"
    if [ "${actual_release}" = "${expected_release}" ]; then
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
  write_service_file "${OLD_RELEASE_COMMIT:-unknown}"
  "${SYSTEMCTL_BIN}" daemon-reload
  "${SYSTEMCTL_BIN}" restart neurun.service

  if ! wait_for_health "${OLD_RELEASE_COMMIT}"; then
    echo "错误：旧版本已恢复，但健康检查仍未通过" >&2
    return 1
  fi

  echo "已恢复旧版本，服务继续运行：${OLD_RELEASE}" >&2
}

# 到这里新 release 已完成构建和导入检查，才切换在线指针。
activate_release "${RELEASE_DIR}"
CURRENT_SWITCHED=1

if ! "${SYSTEMCTL_BIN}" daemon-reload \
  || ! "${SYSTEMCTL_BIN}" enable neurun.service >/dev/null \
  || ! "${SYSTEMCTL_BIN}" restart neurun.service
then
  echo "错误：systemd 未能启动新版本" >&2
  restore_previous_release || true
  exit 1
fi

if wait_for_health "${SOURCE_COMMIT}"; then
  trap - ERR
  echo "neurun 启动成功，release=${RELEASE_ID} commit=${SOURCE_COMMIT}"
  "${SYSTEMCTL_BIN}" --no-pager --full status neurun.service || true
  exit 0
fi

echo "错误：neurun 新版本启动后健康检查未通过" >&2
journalctl -u neurun.service -n 100 --no-pager || true
restore_previous_release || true
exit 1

# ECS 用户数据本地还原

- **适用场景**：ECS 上已获批准的单用户故障排查，需要在受控本地环境还原最小现场。
- **数据边界**：只下载经唯一 `api_key` 确认的 SQLite 数据库、记忆文件和任务状态；不下载 Token 目录、用户注册原件或任何全局文件。
- **安全要求**：昵称可重复且可修改，不能作为定位或导出键；先经受控身份核验锁定唯一 `api_key`。本手册不是对外数据导出流程，完整治理和上线门禁见 [用户关联数据 TAR 导出治理与上线准备](user-data-tar-export-governance.md)。

## 文件结构

ECS 上数据目录为 `/var/lib/neurun/`，每个用户按 `api_key` 隔离：

```text
/var/lib/neurun/
├── users/<api_key>.json          # 用户注册信息（邮箱、昵称、api_key 等）
├── <api_key>/data.db             # SQLite 数据库（活动、健康指标、同步状态）
├── <api_key>/memory/             # 教练记忆（Markdown 日报、方案、周报等）
├── <api_key>/sync-tasks.json     # 同步任务状态
├── <api_key>/tokens/             # Garmin OAuth Token（敏感，排查完删）
└── invite-codes.json             # 邀请码
```

## 步骤一：在受控核验后确认 api_key

```bash
# 由获授权的受理流程提供唯一 api_key；不要按昵称匹配或把邮箱写入 shell 历史。
API_KEY="<经核验的唯一 api_key>"
```

## 步骤二：打包目标用户数据

```bash
cd /var/lib/neurun
tar -czf /tmp/user-data.tar.gz \
  <api_key>/data.db \
  <api_key>/memory/ \
  <api_key>/sync-tasks.json
```

## 步骤三：下载到本地

```bash
scp root@<ecs-ip>:/tmp/user-data.tar.gz /tmp/user-data.tar.gz
mkdir -p /tmp/user-data && tar -xzf /tmp/user-data.tar.gz -C /tmp/user-data
```

或合并为一行（本地直接拉取解压）：

```bash
ssh root@<ecs-ip> 'cd /var/lib/neurun && tar -czf - <api_key>/{data.db,memory,sync-tasks.json}' | tar -xzf - -C /tmp/user-data
```

## 步骤四：导入本地数据目录

```bash
cd /path/to/neurun
API_KEY="<api_key>"

# 复制用户数据
cp -r /tmp/user-data/$API_KEY data/

# 验证数据量
sqlite3 data/$API_KEY/data.db \
  "SELECT COUNT(*) FROM activities; SELECT COUNT(*) FROM daily_health_metrics;"
```

## 步骤五：重置密码

```bash
python3 -c "
import os
os.environ.setdefault('NEURUN_DATA_DIR', './data')
from src.users import UserManager, hash_password
um = UserManager('./data')
um.update('$API_KEY', password_hash=hash_password('temppass123'))
print('密码已重置为: temppass123')
"
```

## 步骤六：启动服务

```bash
NEURUN_SERVE_MODE=1 python3 -m src.main serve --port 8080 --host 127.0.0.1
```

打开 `http://127.0.0.1:8080`，用用户邮箱和重置后的密码登录。

不得通过浏览器开发者工具注入 `neurun_key` Cookie。该值是会话凭据；本地验证应使用临时、经批准的测试账户和正常登录流程。

## 排查完成后清理

```bash
rm -rf /tmp/user-data /tmp/user-data.tar.gz
rm -rf data/<api_key>
```

> 本流程不应下载 `tokens/`、`huawei-tokens/`、用户注册 JSON、邀请码、环境变量或全局备份。若排障确实需要凭据，停止本流程并按专项安全审批处理，不得将凭据放入 TAR 或工单附件。

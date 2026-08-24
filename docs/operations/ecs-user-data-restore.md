# ECS 用户数据本地还原

- **适用场景**：ECS 上用户反馈数据异常、日报/训练为空、同步失败等，需要本地还原现场排查。
- **数据边界**：只下载指定用户的 SQLite 数据库、记忆文件和任务状态；不下载 Token 目录（避免本地与 ECS 争抢刷新）。
- **安全要求**：下载的 `tokens/` 目录含 Garmin OAuth 凭证，排查完成后立即删除。

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

## 步骤一：根据用户邮箱找到 api_key

```bash
# SSH 到 ECS，用邮箱查找
grep -l "75400853@qq.com" /var/lib/neurun/users/*.json

# 确认 api_key
cat /var/lib/neurun/users/<匹配文件>.json | python3 -c "import sys,json; print(json.load(sys.stdin)['api_key'])"
```

## 步骤二：打包目标用户数据

```bash
cd /var/lib/neurun
tar -czf /tmp/user-data.tar.gz \
  <api_key>/data.db \
  <api_key>/memory/ \
  <api_key>/sync-tasks.json \
  <api_key>/tokens/ \
  users/<api_key>.json
```

## 步骤三：下载到本地

```bash
scp root@<ecs-ip>:/tmp/user-data.tar.gz /tmp/user-data.tar.gz
mkdir -p /tmp/user-data && tar -xzf /tmp/user-data.tar.gz -C /tmp/user-data
```

或合并为一行（本地直接拉取解压）：

```bash
ssh root@<ecs-ip> 'cd /var/lib/neurun && tar -czf - <api_key>/{data.db,memory,sync-tasks.json,tokens} users/<api_key>.json' | tar -xzf - -C /tmp/user-data
```

## 步骤四：导入本地数据目录

```bash
cd /path/to/neurun
API_KEY="<api_key>"

# 复制用户数据
cp -r /tmp/user-data/$API_KEY data/
cp /tmp/user-data/users/$API_KEY.json data/users/

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

## 备选：跳过密码直接注入 Cookie

不需要重置密码时，可在浏览器开发者工具中手动添加 Cookie：

| 字段 | 值 |
|------|-----|
| Name | `neurun_key` |
| Value | `<api_key>` |

保存后刷新页面即可。

## 排查完成后清理

```bash
rm -rf /tmp/user-data /tmp/user-data.tar.gz
rm -rf data/<api_key> data/users/<api_key>.json
```

> 若下载了 `tokens/` 目录，务必确认已一并删除，避免 Garmin OAuth 凭证泄露。

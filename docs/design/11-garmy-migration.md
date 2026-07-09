# 设计方案 — 11. garmy 2.0 适配

> 属于 [设计方案索引](../design.md) · 版本 v3.0 · 2026-06-24

---

## 11. 实现备忘（garmy 2.0 适配）

实际实现过程中发现的 garmy 2.0 与文档预期的差异，以及对应的解决方案。

### 11.1 API 差异速查

| 模块 | 预期 (v1.0 文档) | 实际 (v2.0) | 影响 |
|------|-----------------|-------------|------|
| `AuthClient.login()` | `mfa_callback=` | `prompt_mfa=` + `return_on_mfa=True` | auth.py |
| `AuthClient.is_authenticated` | 方法 `.is_authenticated()` | property `.is_authenticated` | auth.py |
| `SyncManager.__init__()` | 接受 `HealthDB` 实例 | 接受 `db_path: Path`（路径字符串） | storage.py |
| `SyncManager.sync_range()` | 需先 `initialize()` | 同，`initialize(email, password)` 内部创建 APIClient | storage.py |
| `HealthDB.get_activities()` | `(user_id)` 单参数 | `(user_id, start_date, end_date)` 日期范围 | storage.py, memory.py |
| `HealthDB.get_health_metrics()` | `(user_id, date)` 单日 | `(user_id, start_date, end_date)` 日期范围，返回列表 | storage.py, memory.py |
| `APIClient.__init__()` | `(auth_client=auth)` | 同，支持 `domain, timeout, retries` 可选参数 | fetcher.py |
| `MetricAccessor.list()` | 复杂筛选 | `.list(days=N)` 简化接口 | fetcher.py |
| `user_id` 来源 | 无明确说明 | `APIClient.profile['id']` → int | main.py |

### 11.2 数据库 Schema（实际）

garmy 2.0 `daily_health_metrics` 表为**单行宽表**，每行包含当天全部健康指标：

```
user_id               INTEGER    Garmin 用户 ID
metric_date           DATE       日期
── 活动 ──
total_steps           INTEGER    步数
step_goal             INTEGER    步数目标
total_distance_meters FLOAT      全天移动距离
total_calories        INTEGER    总消耗卡路里
active_calories       INTEGER    活动消耗
bmr_calories          INTEGER    基础代谢
── 心率 ──
resting_heart_rate    INTEGER    静息心率
max_heart_rate        INTEGER    最大心率
min_heart_rate        INTEGER    最低心率
── 压力 ──
avg_stress_level      INTEGER    平均压力
max_stress_level      INTEGER    最大压力
── 身体电量 ──
body_battery_high     INTEGER    最高电量
body_battery_low      INTEGER    最低电量
── 睡眠 ──
sleep_duration_hours  FLOAT      总时长
deep_sleep_hours      FLOAT      深睡时长
light_sleep_hours     FLOAT      浅睡时长
rem_sleep_hours       FLOAT      REM 时长
deep_sleep_percentage FLOAT      深睡占比
rem_sleep_percentage  FLOAT      REM 占比
── HRV ──
hrv_weekly_avg        FLOAT      7 天均值
hrv_last_night_avg    FLOAT      昨晚均值
hrv_status            TEXT       状态 (BALANCED/UNBALANCED/LOW)
── 训练准备 ──
training_readiness_score  INTEGER 评分
training_readiness_level  TEXT    等级 (HIGH/MODERATE/LOW)
```

`activities` 表：

```
user_id            INTEGER
activity_id        VARCHAR      Garmin 活动 ID
activity_date      DATE         活动日期
activity_name      VARCHAR      活动名称（含类型信息如"厦门市 跑步"）
duration_seconds   INTEGER      持续秒数
avg_heart_rate     INTEGER      平均心率
training_load      FLOAT        训练负荷
start_time         VARCHAR      开始时间
```

### 11.3 关键实现决策

1. **`_get_user_id()`**: 通过 `APIClient(auth_client=auth.client).profile['id']` 获取，返回 `int`。该值在所有 storage/memory 查询中使用。
2. **SyncManager 初始化**: `SyncManager(db_path=str(self._db_path))` 传入路径字符串，SyncManager 内部自行管理 HealthDB 连接。
3. **日报数据流**: `generate_daily_report()` 首先将 raw health data 通过 `_summarize_sleep()` / `_summarize_morning()` 转换为标准化字段名，后续计算均使用标准化名称以保证一致性。
4. **字段映射**: memory.py 中所有数据读取方法均支持新旧两种字段名（如 `resting_hr` / `resting_heart_rate`），确保兼容性。
5. **HTML 渲染**: `src/render.py` 为独立模块，不依赖 garmy。输入为日报 Memory 对象，输出为自包含 HTML 文件到 `output/YYYY-MM-DD.html`。

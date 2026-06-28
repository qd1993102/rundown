"""记忆存储模块 — 在运动数据之上构建结构化运动知识库。

核心设计：
- 数据层 (SQLite) 存储"事实"，记忆层 (Markdown + YAML FM) 存储"认知"
- 自动生成：每日报告、运动摘要、恢复摘要、执行跟踪
- 人工维护：竞技档案、目标管理、训练计划、教练知识
- AI 原生：文件格式天然适合作为 LLM 上下文
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Types
# ═══════════════════════════════════════════════════════════════


class MemoryType(str, Enum):
    """记忆类型枚举。"""
    DAILY_REPORT = "daily_report"
    ACTIVITY_SUMMARY = "activity_summary"
    RECOVERY_SUMMARY = "recovery_summary"
    EXECUTION_TRACKER = "execution_tracker"
    FITNESS_PROFILE = "fitness_profile"
    GOAL = "goal"
    TRAINING_PLAN = "training_plan"
    COACHING_PREFERENCE = "coaching_preference"
    CASE_STUDY = "case_study"
    AI_INSIGHT = "ai_insight"
    INDEX = "index"


class MemoryStatus(str, Enum):
    """记忆状态。"""
    DRAFT = "draft"
    ACTIVE = "active"
    DONE = "done"
    STALE = "stale"
    FROZEN = "frozen"
    ARCHIVED = "archived"


# ═══════════════════════════════════════════════════════════════
# YAML Front Matter Parser
# ═══════════════════════════════════════════════════════════════

_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """解析 Markdown 文件的 YAML Front Matter。

    Returns:
        (front_matter_dict, body_text)
    """
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    body = text[match.end():]
    return fm, body


def build_memory_file(front_matter: dict[str, Any], body: str) -> str:
    """构建带 YAML Front Matter 的 Markdown 内容。"""
    fm_yaml = yaml.dump(
        front_matter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    return f"---\n{fm_yaml}\n---\n\n{body}".strip() + "\n"


# ═══════════════════════════════════════════════════════════════
# Data Classes
# ═══════════════════════════════════════════════════════════════


@dataclass
class Memory:
    """单条记忆。"""
    id: str  # 唯一标识，通常就是文件名（不含 .md）
    type: MemoryType
    path: Path
    front_matter: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    @classmethod
    def from_file(cls, path: Path) -> Memory | None:
        """从文件加载记忆。"""
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            fm, body = parse_front_matter(text)
            memory_id = fm.get("id", path.stem)
            memory_type = MemoryType(fm.get("type", "activity_summary"))
            return cls(
                id=memory_id,
                type=memory_type,
                path=path,
                front_matter=fm,
                body=body,
            )
        except Exception as exc:
            logger.warning("加载记忆文件失败 %s: %s", path, exc)
            return None

    def save(self) -> None:
        """保存记忆到文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = build_memory_file(self.front_matter, self.body)
        self.path.write_text(content, encoding="utf-8")

    @property
    def status(self) -> MemoryStatus | None:
        """获取记忆状态。"""
        s = self.front_matter.get("status")
        return MemoryStatus(s) if s else None

    @property
    def tags(self) -> list[str]:
        """获取标签列表。"""
        return self.front_matter.get("tags", [])

    @property
    def created_date(self) -> date | None:
        """获取创建日期。"""
        d = self.front_matter.get("created") or self.front_matter.get("date")
        if d is None:
            return None
        if isinstance(d, date):
            return d
        return date.fromisoformat(str(d)[:10])

    def __repr__(self) -> str:
        return f"Memory(id={self.id!r}, type={self.type.value})"


# ═══════════════════════════════════════════════════════════════
# MemoryReader
# ═══════════════════════════════════════════════════════════════


class MemoryReader:
    """记忆读取器 — 纯读操作，无副作用。"""

    def __init__(self, memory_dir: str):
        self._root = Path(memory_dir)

    def get(self, memory_id: str) -> Memory | None:
        """按 ID 获取单条记忆。"""
        for path in self._root.rglob(f"{memory_id}.md"):
            return Memory.from_file(path)
        # 也尝试直接匹配文件名
        for path in self._root.rglob("*.md"):
            if path.stem == memory_id:
                return Memory.from_file(path)
        return None

    def get_latest(self, memory_type: MemoryType) -> Memory | None:
        """获取最新一条某类型记忆。"""
        memories = self.list_by_type(memory_type)
        if not memories:
            return None
        # 按创建日期降序排序
        dated = [(m, m.created_date or date.min) for m in memories]
        dated.sort(key=lambda x: x[1], reverse=True)
        return dated[0][0]

    def list_by_type(
        self,
        memory_type: MemoryType,
        status: MemoryStatus | None = None,
        tags: list[str] | None = None,
        date_range: tuple[date, date] | None = None,
    ) -> list[Memory]:
        """按类型列出记忆，支持筛选。"""
        type_dirs = self._type_to_dirs(memory_type)
        memories: list[Memory] = []

        for d in type_dirs:
            if not d.exists():
                continue
            for path in sorted(d.rglob("*.md")):
                mem = Memory.from_file(path)
                if mem is None:
                    continue
                # 筛选
                if status and mem.status != status:
                    continue
                if tags and not any(t in mem.tags for t in tags):
                    continue
                if date_range:
                    cd = mem.created_date
                    if cd and not (date_range[0] <= cd <= date_range[1]):
                        continue
                memories.append(mem)

        return memories

    def query(
        self,
        tags: list[str] | None = None,
        date_range: tuple[date, date] | None = None,
    ) -> list[Memory]:
        """按标签和日期范围组合查询。"""
        results: list[Memory] = []
        for path in sorted(self._root.rglob("*.md")):
            if path.parent.name == "archive":
                continue
            mem = Memory.from_file(path)
            if mem is None:
                continue
            if tags and not any(t in mem.tags for t in tags):
                continue
            if date_range:
                cd = mem.created_date
                if cd and not (date_range[0] <= cd <= date_range[1]):
                    continue
            results.append(mem)
        return results

    def search(self, keyword: str) -> list[Memory]:
        """全文搜索记忆（搜索正文内容）。"""
        results: list[Memory] = []
        kw = keyword.lower()
        for path in sorted(self._root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8").lower()
                if kw in text:
                    mem = Memory.from_file(path)
                    if mem:
                        results.append(mem)
            except Exception:
                continue
        return results

    def get_index(self, category: str) -> Memory | None:
        """获取某分类的索引文件。"""
        index_path = self._root / category / "index.md"
        return Memory.from_file(index_path)

    def _type_to_dirs(self, memory_type: MemoryType) -> list[Path]:
        """将记忆类型映射到目录。"""
        mapping: dict[MemoryType, list[str]] = {
            MemoryType.DAILY_REPORT: ["auto/daily"],
            MemoryType.ACTIVITY_SUMMARY: ["auto/summaries"],
            MemoryType.RECOVERY_SUMMARY: ["auto/recovery"],
            MemoryType.EXECUTION_TRACKER: ["auto/execution"],
            MemoryType.FITNESS_PROFILE: ["profile"],
            MemoryType.GOAL: ["goals/active", "goals/completed", "goals/archived"],
            MemoryType.TRAINING_PLAN: [
                "plans/active", "plans/completed", "plans/templates",
            ],
            MemoryType.COACHING_PREFERENCE: ["coaching"],
            MemoryType.CASE_STUDY: ["coaching/cases"],
            MemoryType.AI_INSIGHT: ["coaching/ai-insights", "coaching/insights"],
            MemoryType.INDEX: [""],
        }
        dirs = mapping.get(memory_type, [])
        return [self._root / d for d in dirs]


# ═══════════════════════════════════════════════════════════════
# MemoryWriter
# ═══════════════════════════════════════════════════════════════


class MemoryWriter:
    """记忆写入器 — 自动生成 + 人工创建。"""

    def __init__(
        self,
        memory_dir: str,
        db_getter: Callable[[], Any] | None = None,
        api_client_getter: Callable[[], Any] | None = None,
        memory_store=None,
    ):
        self._root = Path(memory_dir)
        self._db_getter = db_getter  # 延迟获取 HealthDB
        self._api_client_getter = api_client_getter  # 延迟获取 garmy.APIClient
        self._memory_store = memory_store  # MemoryStore 引用，用于读取 profile/goals

    def _api(self):
        """获取数据平台 API 客户端（用于补全活动距离）。"""
        if self._api_client_getter is None:
            return None
        return self._api_client_getter()

    def _db(self):
        """获取数据库实例。"""
        if self._db_getter is None:
            raise RuntimeError("MemoryWriter 未配置数据库访问")
        return self._db_getter()

    # ── 每日报告 ⭐ ────────────────────────────

    def generate_daily_report(
        self,
        user_id: str,
        target_date: date | None = None,
        ai_insight: dict[str, Any] | None = None,
    ) -> Memory:
        """生成每日综合报告。

        从 SQLite 查询：
        - 今日活动（当天训练数据）
        - 昨夜睡眠（前一天晚上的恢复数据）
        - 今晨状态指标
        - 7 日趋势
        - 异常检测
        - 训练建议

        Args:
            user_id: 运动平台用户 ID。
            target_date: 报告日期，默认今天。

        Returns:
            生成的 Memory 对象。
        """
        if target_date is None:
            target_date = date.today()

        yesterday = target_date - timedelta(days=1)
        db = self._db()

        # 1. 查询今日活动（当天训练数据）
        activities = self._safe_get_activities(db, user_id, target_date, target_date)
        # 2. 从数据平台 API 补全活动距离
        activities = self._enrich_activity_distances(activities, self._api())
        # 同时查今日的健康数据（用于步数等全天活动量）
        today_health = self._safe_get_health(db, user_id, target_date)

        # 3. 查询昨夜睡眠 + 今晨状态（garmy 2.0: 所有健康数据在一行中）
        health_data = self._safe_get_health(db, user_id, target_date)
        sleep = health_data or {}
        morning = health_data or {}

        # 4. 查询近 7 天数据用于趋势
        seven_day_metrics = self._safe_get_health_range(db, user_id, 7)

        # 5. 查询近 28 天数据用于 chronic load（包含今天）
        twenty_eight_day_activities = self._safe_get_activities(
            db, user_id, target_date - timedelta(days=28), target_date,
        )

        # ── 先汇总各维度（供 Front Matter 和 recommendation 共用） ──
        activity_summary = self._summarize_activities(activities, today_health)
        sleep_summary = self._summarize_sleep(sleep)
        morning_summary = self._summarize_morning(morning)

        # ── 构建 Front Matter ──
        fm: dict[str, Any] = {
            "type": "daily_report",
            "date": str(target_date),
            "generated": datetime.now().isoformat(timespec="seconds"),
            "version": 1,
            # 当日活动
            "yesterday_activities": activity_summary,
            # 昨夜睡眠
            "last_night_sleep": sleep_summary,
            # 今晨状态
            "this_morning": morning_summary,
            # 训练负荷
            "training_load": self._calc_training_load(
                activities, twenty_eight_day_activities,
            ),
            # 恢复评分
            "recovery": self._calc_recovery_score(sleep, morning),
            # 7 日趋势
            "trends_7d": self._calc_trends(seven_day_metrics),
            # 异常检测
            "anomalies": self._detect_anomalies(seven_day_metrics),
            # 今日建议（使用汇总后的数据）
            "recommendation": self._generate_recommendation(
                sleep_summary, morning_summary, activities, twenty_eight_day_activities,
            ),
            # 标签
            "tags": self._build_tags(activities, target_date),
        }

        # 10. AI 教练洞察
        if ai_insight is not None:
            fm["ai_insight"] = ai_insight
        else:
            profile = MemoryWriter._load_profile(self) if self._memory_store else None
            goals = MemoryWriter._load_active_goals(self) if self._memory_store else None
            ai_insight = self._generate_ai_insight(
                activity_summary, sleep_summary, morning_summary,
                fm["training_load"], fm["recovery"], fm["anomalies"],
                seven_day_metrics,
                profile=profile, goals=goals,
            )
            fm["ai_insight"] = ai_insight

        # ── 活动详情分析（from activity.py）──
        session_analyses = []
        if not activity_summary.get("is_rest_day"):
            try:
                from .activity import build_session_analysis_text
                # 通过 storage 实例访问 DB
                # 注意：memory.py 没有直接 storage 引用，通过 db_getter 获取
                for a in activities:
                    aid = a.get("activity_id", "")
                    name = a.get("activity_name", "")
                    if aid:
                        text = build_session_analysis_text(db, aid, name)
                        if text:
                            session_analyses.append(text)
            except Exception as exc:
                logger.warning("获取活动详情分析失败: %s", exc)
        fm["session_analyses"] = session_analyses

        # ── 构建正文 ──
        body = self._render_daily_body(fm, target_date)

        # ── 写入文件 ──
        file_path = self._root / "auto" / "daily" / f"{target_date}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        memory = Memory(
            id=str(target_date),
            type=MemoryType.DAILY_REPORT,
            path=file_path,
            front_matter=fm,
            body=body,
        )
        memory.save()

        logger.info("📰 日报已生成: %s", file_path)
        return memory

    # ── 周/月摘要 ──────────────────────────────

    def generate_weekly_summary(
        self, user_id: str, target_date: date | None = None,
    ) -> Memory:
        """生成周运动摘要。"""
        if target_date is None:
            target_date = date.today()

        monday = target_date - timedelta(days=target_date.weekday())
        sunday = monday + timedelta(days=6)
        week_num = monday.isocalendar()[1]

        db = self._db()
        activities = self._safe_get_activities(db, user_id, monday, sunday)

        # 聚合统计
        stats = self._aggregate_activity_stats(activities)

        # 训练负荷
        prev_monday = monday - timedelta(days=7)
        prev_activities = self._safe_get_activities(
            db, user_id, prev_monday, monday - timedelta(days=1),
        )
        prev_stats = self._aggregate_activity_stats(prev_activities)

        # 对比
        vs_last = {}
        if prev_stats["total_duration_min"] > 0:
            vs_last["duration_change_pct"] = round(
                (stats["total_duration_min"] - prev_stats["total_duration_min"])
                / prev_stats["total_duration_min"] * 100, 1,
            )
        if prev_stats.get("total_distance_km", 0) > 0:
            vs_last["distance_change_pct"] = round(
                (stats.get("total_distance_km", 0) - prev_stats.get("total_distance_km", 0))
                / prev_stats.get("total_distance_km", 1) * 100, 1,
            )

        fm: dict[str, Any] = {
            "type": "activity_summary",
            "period": "weekly",
            "start_date": str(monday),
            "end_date": str(sunday),
            "week_number": week_num,
            "year": target_date.year,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "stats": stats,
            "by_type": stats.get("by_type", {}),
            "training_load": self._calc_training_load(activities, []),
            "vs_last_week": vs_last,
            "tags": [t for t in stats.get("types", [])],
        }

        body = self._render_summary_body(fm, "weekly")

        file_path = self._root / "auto" / "summaries" / f"{target_date.year}-W{week_num:02d}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        memory = Memory(
            id=f"{target_date.year}-W{week_num:02d}",
            type=MemoryType.ACTIVITY_SUMMARY,
            path=file_path,
            front_matter=fm,
            body=body,
        )
        memory.save()
        logger.info("📊 周摘要已生成: %s", file_path)
        return memory

    def generate_recovery_summary(
        self, user_id: str, target_date: date | None = None,
    ) -> Memory:
        """生成周恢复摘要。"""
        if target_date is None:
            target_date = date.today()

        monday = target_date - timedelta(days=target_date.weekday())
        sunday = monday + timedelta(days=6)
        week_num = monday.isocalendar()[1]

        db = self._db()
        metrics_list = self._safe_get_health_range(db, user_id, 7)

        # 聚合
        sleep_avg = self._avg_metric(metrics_list, "sleep_duration_hours", "sleep")
        hrv_avg = self._avg_metric(metrics_list, "hrv_avg", "hrv")
        hr_avg = self._avg_metric(metrics_list, "resting_heart_rate", "heart_rate")
        stress_avg = self._avg_metric(metrics_list, "avg_stress", "stress")

        fm: dict[str, Any] = {
            "type": "recovery_summary",
            "period": "weekly",
            "start_date": str(monday),
            "end_date": str(sunday),
            "week_number": week_num,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "sleep": {
                "avg_duration_hours": round(sleep_avg, 1) if sleep_avg else None,
                "trend": "stable",
            },
            "hrv": {
                "weekly_avg_ms": round(hrv_avg, 1) if hrv_avg else None,
                "status": "balanced",
                "trend": "stable",
            },
            "resting_hr": {
                "avg_bpm": round(hr_avg, 1) if hr_avg else None,
                "trend": "stable",
            },
            "stress": {
                "avg_daily": round(stress_avg, 1) if stress_avg else None,
                "trend": "stable",
            },
            "recovery_score": self._calc_recovery_score(
                {}, {"sleep": sleep_avg, "hrv": hrv_avg, "resting_hr": hr_avg},
            ),
            "tags": ["recovery"],
        }

        body = self._render_recovery_body(fm)

        file_path = self._root / "auto" / "recovery" / f"{target_date.year}-W{week_num:02d}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        memory = Memory(
            id=f"recovery-{target_date.year}-W{week_num:02d}",
            type=MemoryType.RECOVERY_SUMMARY,
            path=file_path,
            front_matter=fm,
            body=body,
        )
        memory.save()
        logger.info("💤 恢复摘要已生成: %s", file_path)
        return memory

    def rebuild_index(self, category: str) -> None:
        """重建指定分类的 index.md。"""
        cat_dir = self._root / category
        if not cat_dir.exists():
            return

        entries = []
        for path in sorted(cat_dir.rglob("*.md")):
            if path.name == "index.md":
                continue
            mem = Memory.from_file(path)
            if mem is None:
                continue
            entries.append({
                "file": path.name,
                "id": mem.id,
                "type": mem.type.value,
                "date": str(mem.created_date) if mem.created_date else None,
                "title": mem.front_matter.get("title", path.stem),
            })

        fm: dict[str, Any] = {
            "type": "index",
            "category": category,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "entries": entries,
        }
        body = f"# {category} 索引\n\n"
        for e in entries:
            body += f"- [{e['title']}]({e['file']}) — {e.get('date', '')}\n"

        index_path = cat_dir / "index.md"
        content = build_memory_file(fm, body)
        index_path.write_text(content, encoding="utf-8")
        logger.info("📑 索引已重建: %s (%d 条)", index_path, len(entries))

    # ── 辅助: 数据查询 (带容错) ────────────────

    @staticmethod
    def _safe_get_activities(
        db: Any, user_id: int, start: date, end: date,
    ) -> list[dict[str, Any]]:
        """从数据库获取指定日期范围的活动。

        先尝试含 distance_meters 列的查询（需要 schema migration），
        如果列不存在则回退到不含该列的查询。
        """
        try:
            from sqlalchemy import text
            session = db.get_session()
            rows = session.execute(text("""
                SELECT user_id, activity_id, activity_date, activity_name,
                       duration_seconds, avg_heart_rate, training_load,
                       start_time, distance_meters, created_at
                FROM activities
                WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                ORDER BY start_time
            """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            session.close()
            return [
                {
                    "user_id": r[0], "activity_id": r[1], "activity_date": r[2],
                    "activity_name": r[3], "duration_seconds": r[4],
                    "avg_heart_rate": r[5], "training_load": r[6],
                    "start_time": r[7], "distance_meters": r[8], "created_at": r[9],
                }
                for r in rows
            ]
        except Exception:
            # distance_meters 列可能不存在，回退到不含该列的查询
            return MemoryWriter._safe_get_activities_fallback(
                db, user_id, start, end,
            )

    @staticmethod
    def _safe_get_activities_fallback(
        db: Any, user_id: int, start: date, end: date,
    ) -> list[dict[str, Any]]:
        """回退查询：不含 distance_meters 列（兼容旧 schema）。"""
        try:
            from sqlalchemy import text
            session = db.get_session()
            rows = session.execute(text("""
                SELECT user_id, activity_id, activity_date, activity_name,
                       duration_seconds, avg_heart_rate, training_load,
                       start_time, created_at
                FROM activities
                WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                ORDER BY start_time
            """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            session.close()
            return [
                {
                    "user_id": r[0], "activity_id": r[1], "activity_date": r[2],
                    "activity_name": r[3], "duration_seconds": r[4],
                    "avg_heart_rate": r[5], "training_load": r[6],
                    "start_time": r[7], "distance_meters": None, "created_at": r[8],
                }
                for r in rows
            ]
        except Exception:
            return []

    @staticmethod
    def _enrich_activity_distances(
        activities: list[dict[str, Any]],
        api_client: Any | None = None,
    ) -> list[dict[str, Any]]:
        """用数据平台 API 补全活动距离。

        对于 distance_meters 为空的活动，从数据平台 API 拉取详情。

        Args:
            activities: 活动列表（原地修改）。
            api_client: garmy APIClient 实例（不传则跳过补全）。

        Returns:
            补全后的活动列表。
        """
        if not activities or not api_client:
            return activities

        for a in activities:
            if a.get("distance_meters"):
                continue
            aid = a.get("activity_id", "")
            if not aid:
                continue
            try:
                detail = api_client.connectapi(
                    f"/activity-service/activity/{aid}"
                )
                if detail:
                    summary = detail.get("summaryDTO", {})
                    distance = summary.get("distance", 0) or 0
                    a["distance_meters"] = distance
                    a["calories"] = summary.get("calories", 0) or 0
                    avg_pace = summary.get("averagePaceInSecondsPerKilometer", 0)
                    if avg_pace:
                        a["avg_pace_sec_per_km"] = avg_pace
                    elevation_gain = summary.get("elevationGain", 0)
                    if elevation_gain:
                        a["elevation_gain"] = elevation_gain
            except Exception as exc:
                logger.debug("补全活动 %s 距离失败: %s", aid, exc)

        return activities

    @staticmethod
    def _safe_get_health(
        db: Any, user_id: int, target_date: date,
    ) -> dict[str, Any] | None:
        """获取某天健康指标（garmy 2.0: 返回列表取第一条）。"""
        try:
            results = db.get_health_metrics(user_id, target_date, target_date)
            if not results:
                return None
            return results[0] if isinstance(results, list) else results
        except Exception:
            return None

    @staticmethod
    def _safe_get_health_range(
        db: Any, user_id: int, days: int,
    ) -> list[dict[str, Any]]:
        """获取最近 N 天健康指标。"""
        end = date.today()
        start = end - timedelta(days=days)
        try:
            return db.get_health_metrics(user_id, start, end)
        except Exception:
            return []

    # ── 辅助: 数据聚合 ────────────────────────

    @staticmethod
    def _summarize_activities(
        activities: list[dict[str, Any]],
        health_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """汇总当日活动数据。

        garmy 2.0 存储格式: duration_seconds, avg_heart_rate, training_load, 等扁平字段。
        同时从 health_data 提取全天活动量（步数、距离、卡路里），
        即使没有正式运动记录也能反映活动水平。
        """
        # 全天活动量（来自健康数据，无论是否有正式运动记录）
        daily_steps = 0
        daily_distance_m = 0
        daily_calories = 0
        daily_active_cal = 0
        if health_data:
            daily_steps = health_data.get("total_steps", 0) or 0
            daily_distance_m = health_data.get("total_distance_meters", 0) or 0
            daily_calories = health_data.get("total_calories", 0) or 0
            daily_active_cal = health_data.get("active_calories", 0) or 0

        # 活动水平判定（基于步数和活动卡路里）
        if daily_steps >= 20000 or daily_active_cal >= 500:
            activity_level = "very_active"
        elif daily_steps >= 10000 or daily_active_cal >= 250:
            activity_level = "active"
        elif daily_steps >= 5000:
            activity_level = "light"
        else:
            activity_level = "sedentary"

        # 距离从活动数据中获取（优先 distance_meters，其次 distance）
        # 注意：训练距离 ≠ 全天距离（daily_distance_km 包含了步行步数估算）
        # 不把 health_data 的全天距离混入训练距离
        def _get_distance(a: dict) -> float:
            d = a.get("distance_meters") or a.get("distance", 0) or 0
            return d if d > 0 else 0

        if not activities:
            return {
                "is_rest_day": True,
                "is_training_day": False,
                "sessions": [],
                "total_sessions": 0,
                "total_duration_min": 0,
                "total_distance_km": 0,
                "total_calories": 0,
                "total_training_load": 0,
                "day_type": "rest",
                # 全天活动量（来自 health_data）
                "daily_steps": daily_steps,
                "daily_distance_km": round(daily_distance_m / 1000, 2),
                "daily_calories": daily_calories,
                "daily_active_cal": daily_active_cal,
                "activity_level": activity_level,
            }

        sessions = []
        total_duration_sec = 0
        total_distance = 0
        total_calories = 0
        total_load = 0

        for a in activities:
            dur_sec = a.get("duration_seconds", 0) or 0
            dist = _get_distance(a)
            cal = a.get("total_calories", 0) or a.get("calories", 0) or 0
            load = a.get("training_load", 0) or a.get("activity_training_load", 0) or 0
            hr = a.get("avg_heart_rate") or a.get("average_hr")

            total_duration_sec += dur_sec
            total_distance += dist
            total_calories += cal
            total_load += load

            activity_name = a.get("activity_name", "")
            if "跑" in activity_name or "run" in activity_name.lower():
                atype = "running"
            elif "骑" in activity_name or "cycling" in activity_name.lower():
                atype = "cycling"
            elif "游泳" in activity_name or "swim" in activity_name.lower():
                atype = "swimming"
            elif "力量" in activity_name or "strength" in activity_name.lower():
                atype = "strength"
            else:
                atype = activity_name or "unknown"

            sessions.append({
                "type": atype,
                "name": activity_name,
                "duration_min": round(dur_sec / 60, 1),
                "distance_km": round(dist / 1000, 2) if dist else None,
                "avg_hr": hr,
                "training_load": round(load, 1) if load else 0,
                "calories": cal,
            })

        total_dur_min = round(total_duration_sec / 60, 1)

        if total_load > 200:
            day_type = "workout"
        elif total_duration_sec > 3600:
            day_type = "long_run" if total_distance > 10000 else "workout"
        elif total_duration_sec > 0:
            day_type = "easy_run"
        else:
            day_type = "rest"

        # 训练距离：优先用活动详情距离，没有时用全天距离（仅当全天距离合理时）
        training_distance_km = round(total_distance / 1000, 2)
        if training_distance_km <= 0 and daily_distance_m > 0:
            # 没有活动详情距离时的回退：用全天距离（可能包含步行，但总比没有好）
            training_distance_km = round(daily_distance_m / 1000, 2)

        return {
            "is_rest_day": False,
            "is_training_day": total_duration_sec > 0,
            "sessions": sessions,
            "total_sessions": len(sessions),
            "total_duration_min": total_dur_min,
            "total_distance_km": training_distance_km,
            "total_calories": total_calories,
            "total_training_load": round(total_load, 1),
            "day_type": day_type,
            # 全天活动量（来自 health_data，独立于训练数据）
            "daily_steps": daily_steps,
            "daily_distance_km": round(daily_distance_m / 1000, 2),
            "daily_calories": daily_calories,
            "daily_active_cal": daily_active_cal,
            "activity_level": activity_level,
        }

    @staticmethod
    def _summarize_sleep(sleep_data: dict[str, Any]) -> dict[str, Any]:
        """汇总睡眠数据。

        garmy 2.0 存储格式: sleep_duration_hours, deep_sleep_hours, rem_sleep_hours 等扁平字段。
        """
        if not sleep_data:
            return {"quality": "unknown"}

        total_hours = (
            sleep_data.get("sleep_duration_hours", 0) or 0
        )
        deep_pct = sleep_data.get("deep_sleep_percentage") or 0
        rem_pct = sleep_data.get("rem_sleep_percentage") or 0

        # 睡眠质量判定（基于时长 + 深睡占比）
        if total_hours >= 8 and deep_pct >= 20:
            quality = "excellent"
        elif total_hours >= 7 and deep_pct >= 15:
            quality = "good"
        elif total_hours >= 6:
            quality = "fair"
        elif total_hours > 0:
            quality = "poor"
        else:
            quality = "unknown"

        # 睡眠评分估算（简化）
        score_base = min(total_hours / 9 * 100, 100) if total_hours > 0 else 0
        score = round(score_base * 0.7 + (deep_pct * 2 if deep_pct else 40) * 0.3)

        return {
            "total_hours": round(total_hours, 1),
            "sleep_score": score,
            "quality": quality,
            "deep_sleep_hours": sleep_data.get("deep_sleep_hours"),
            "rem_sleep_hours": sleep_data.get("rem_sleep_hours"),
            "deep_sleep_pct": deep_pct,
            "rem_sleep_pct": rem_pct,
            "avg_spo2": sleep_data.get("average_spo2"),
            "avg_respiration": sleep_data.get("average_respiration"),
        }

    @staticmethod
    def _summarize_morning(morning: dict[str, Any]) -> dict[str, Any]:
        """汇总今晨状态。

        garmy 2.0 存储格式: resting_heart_rate, hrv_last_night_avg, body_battery_high 等。
        """
        return {
            "resting_hr": morning.get("resting_heart_rate"),
            "hrv_ms": morning.get("hrv_last_night_avg"),
            "hrv_7d_avg": morning.get("hrv_weekly_avg"),
            "hrv_status": morning.get("hrv_status", "balanced"),
            "body_battery_morning": morning.get("body_battery_high"),
            "body_battery_low": morning.get("body_battery_low"),
            "training_readiness_score": morning.get("training_readiness_score"),
            "training_readiness_level": morning.get("training_readiness_level"),
            "avg_stress": morning.get("avg_stress_level"),
            "max_stress": morning.get("max_stress_level"),
        }

    @staticmethod
    def _calc_training_load(
        recent: list[dict[str, Any]],
        chronic_activities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """计算训练负荷 (ACWR)。"""
        acute = sum(a.get("activity_training_load", 0) or 0 for a in recent)
        chronic = (
            sum(a.get("activity_training_load", 0) or 0 for a in chronic_activities)
        ) / max(len(chronic_activities), 1)

        if chronic > 0:
            acwr = round(acute / chronic, 2)
        else:
            acwr = 1.0

        if acwr < 0.8:
            status = "undertraining"
        elif acwr <= 1.3:
            status = "optimal"
        elif acwr <= 1.5:
            status = "overreaching"
        else:
            status = "high_risk"

        return {
            "acute_load_7d": acute,
            "chronic_load_28d": round(chronic, 1),
            "acwr": acwr,
            "acwr_status": status,
        }

    @staticmethod
    def _calc_recovery_score(
        sleep: dict[str, Any], morning: dict[str, Any],
    ) -> dict[str, Any]:
        """计算综合恢复评分 (0-100)。

        接受 raw health data 或 summarized data，优先使用已汇总的字段名。
        """
        weights = {
            "sleep": 30,
            "hrv": 25,
            "resting_hr": 15,
            "stress": 10,
            "battery": 10,
            "readiness": 10,
        }
        total_weight = 0
        score = 0

        # 睡眠分（支持两种字段名）
        sleep_hours = (
            sleep.get("total_hours")
            or sleep.get("sleep_duration_hours", 0) or 0
        )
        sleep_score_val = sleep.get("sleep_score")
        if sleep_score_val is None:
            if sleep_hours > 0:
                sleep_score_val = min(sleep_hours / 9 * 100, 95)
            else:
                sleep_score_val = 70  # 数据缺失时假设正常，不惩罚
        if sleep_score_val > 100:
            sleep_score_val = sleep_score_val / 10
        score += sleep_score_val * weights["sleep"] / 100
        total_weight += weights["sleep"]

        # HRV 分
        hrv_status = morning.get("hrv_status", "balanced") or "balanced"
        hrv_map = {"balanced": 80, "unbalanced": 50, "low": 30}
        hrv_score = hrv_map.get(str(hrv_status).upper(), 60)
        score += hrv_score * weights["hrv"] / 100
        total_weight += weights["hrv"]

        # 静息心率分 (支持两种字段名)
        rhr = (
            morning.get("resting_hr")
            or morning.get("resting_heart_rate", 50) or 50
        )
        if 38 <= rhr <= 55:
            hr_score = 90
        elif rhr <= 65:
            hr_score = 75
        elif rhr <= 75:
            hr_score = 55
        else:
            hr_score = 35
        score += hr_score * weights["resting_hr"] / 100
        total_weight += weights["resting_hr"]

        # 压力分
        stress = morning.get("avg_stress") or morning.get("avg_stress_level", 25) or 25
        if stress <= 25:
            stress_score = 85
        elif stress <= 40:
            stress_score = 65
        else:
            stress_score = 45
        score += stress_score * weights["stress"] / 100
        total_weight += weights["stress"]

        # 身体电量分
        battery = (
            morning.get("body_battery_morning")
            or morning.get("body_battery_high", 70) or 70
        )
        battery_score = min(battery, 100)
        score += battery_score * weights["battery"] / 100
        total_weight += weights["battery"]

        # 训练准备分
        readiness = (
            morning.get("training_readiness_score")
            or 70
        )
        readiness_score_val = min(readiness or 70, 100)
        score += readiness_score_val * weights["readiness"] / 100
        total_weight += weights["readiness"]

        final = round(score / total_weight * 100) if total_weight > 0 else 70

        if final >= 80:
            level = "excellent"
        elif final >= 65:
            level = "good"
        elif final >= 50:
            level = "fair"
        else:
            level = "poor"

        # 识别限制因素
        limiting = "none"
        if sleep_hours < 7:
            limiting = "sleep_duration"
        elif hrv_status and str(hrv_status).upper() in ("LOW", "UNBALANCED"):
            limiting = "hrv"
        elif (rhr or 50) > 65:
            limiting = "resting_hr"

        return {
            "overall_score": final,
            "level": level,
            "limiting_factor": limiting,
        }

    @staticmethod
    def _calc_trends(
        metrics_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """计算 7 日趋势。"""
        trends: dict[str, Any] = {}
        metrics_keys = ["hrv_avg", "resting_heart_rate", "sleep_score"]
        for key in metrics_keys:
            values = [m.get(key) for m in metrics_list if m.get(key) is not None]
            if len(values) >= 3:
                # 简单线性趋势
                n = len(values)
                x_mean = (n - 1) / 2
                y_mean = sum(values) / n
                num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
                den = sum((i - x_mean) ** 2 for i in range(n))
                slope = num / den if den != 0 else 0

                if slope > 0.5:
                    direction = "improving"
                elif slope < -0.5:
                    direction = "declining"
                else:
                    direction = "stable"

                trends[key] = {"values": values, "slope": round(slope, 2), "direction": direction}

        return trends

    @staticmethod
    def _detect_anomalies(
        metrics_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """异常检测。"""
        items: list[dict[str, Any]] = []

        # 睡眠不足检测
        sleep_scores = [
            m.get("sleep_score", 0) or 0 for m in metrics_list
            if m.get("sleep_score") is not None
        ]
        if sleep_scores and sum(1 for s in sleep_scores if s < 65) >= 3:
            items.append({
                "type": "sleep_score_low",
                "severity": "warning",
                "message": "近 7 天睡眠评分多次低于 65",
                "detail": "睡眠质量问题可能影响恢复，建议关注睡眠环境与作息规律",
            })

        if not items:
            return {"count": 0, "level": "normal", "items": []}

        count = len(items)
        level = "critical" if count >= 3 else "warning"
        return {"count": count, "level": level, "items": items}

    @staticmethod
    def _generate_recommendation(
        sleep: dict[str, Any],
        morning: dict[str, Any],
        activities: list[dict[str, Any]],
        chronic_activities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """生成今日训练建议。"""
        load = MemoryWriter._calc_training_load(activities, chronic_activities)
        recovery = MemoryWriter._calc_recovery_score(sleep, morning)

        ready = (
            recovery["level"] in ("excellent", "good", "fair")
            and load["acwr_status"] != "high_risk"
        )

        rec_level = recovery["level"]
        acwr_stat = load["acwr_status"]

        if rec_level == "excellent" and acwr_stat == "optimal":
            intensity = "hard"
            advice = "适合高强度训练"
        elif rec_level in ("excellent", "good") and acwr_stat == "optimal":
            intensity = "moderate"
            advice = "适合中等强度训练"
        elif rec_level == "fair" or acwr_stat == "overreaching":
            intensity = "easy"
            advice = "建议轻松训练或主动恢复"
        elif rec_level == "poor" or acwr_stat == "high_risk":
            intensity = "rest"
            ready = False
            advice = "建议休息日，让身体充分恢复"
        else:
            intensity = "moderate"
            advice = "适合中等强度训练"

        sleep_hours = sleep.get("total_hours", 0) or 0
        cautions = []
        if 0 < sleep_hours < 7:
            cautions.append("睡眠不足 7h，训练中注意补水和倾听身体信号")
        resting_hr = morning.get("resting_hr") or morning.get("resting_heart_rate", 50) or 50
        if resting_hr > 55:
            cautions.append(f"晨起心率偏高 ({resting_hr} bpm)，注意观察身体反应")

        return {
            "ready_to_train": ready,
            "training_advice": advice,
            "intensity": intensity,
            "caution": cautions,
            "focus_areas": ["技术动作", "核心力量"],
        }

    @staticmethod
    def _build_tags(
        activities: list[dict[str, Any]], target_date: date,
    ) -> list[str]:
        """构建标签列表。"""
        tags = ["daily", str(target_date)]
        for a in activities:
            atype = a.get("activity_type_name", "")
            if atype and atype not in tags:
                tags.append(atype)
        return tags

    @staticmethod
    def _aggregate_activity_stats(
        activities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """聚合活动统计。"""
        total_dur = sum(a.get("duration", 0) or 0 for a in activities)
        total_dist = sum(a.get("distance", 0) or 0 for a in activities)
        total_cal = sum(a.get("calories", 0) or 0 for a in activities)
        total_load = sum(a.get("activity_training_load", 0) or 0 for a in activities)

        by_type: dict[str, Any] = {}
        types_set: set[str] = set()
        for a in activities:
            atype = a.get("activity_type_name", "unknown")
            types_set.add(atype)
            if atype not in by_type:
                by_type[atype] = {"count": 0, "duration_min": 0, "distance_km": 0}
            by_type[atype]["count"] += 1
            by_type[atype]["duration_min"] += round(
                (a.get("duration", 0) or 0) / 60, 1,
            )
            dist = (a.get("distance", 0) or 0) / 1000
            by_type[atype]["distance_km"] += round(dist, 2)

        return {
            "total_activities": len(activities),
            "total_duration_min": round(total_dur / 60, 1),
            "total_distance_km": round(total_dist / 1000, 2),
            "total_calories": total_cal,
            "total_training_load": total_load,
            "by_type": by_type,
            "types": list(types_set),
        }

    # ── 辅助: 平均 ────────────────────────────

    @staticmethod
    def _avg_metric(
        metrics_list: list[dict[str, Any]], key: str, prefix: str = "",
    ) -> float | None:
        values = []
        for m in metrics_list:
            v = m.get(key)
            if v is None and prefix:
                nested = m.get(prefix, {})
                if isinstance(nested, dict):
                    v = nested.get(key)
            if v is not None:
                values.append(v)
        return sum(values) / len(values) if values else None

    # ── AI 教练洞察 ───────────────────────────

    @staticmethod
    def _generate_ai_insight(
        activity: dict[str, Any],
        sleep: dict[str, Any],
        morning: dict[str, Any],
        load: dict[str, Any],
        recovery: dict[str, Any],
        anomalies: dict[str, Any],
        seven_day: list[dict[str, Any]],
        profile: dict[str, Any] | None = None,
        goals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """基于数据规则生成 AI 教练自然语言洞察。

        结合运动员画像（竞技水平、目标）给出个性化分析。
        """
        observations: list[str] = []
        recommendations: list[str] = []
        warnings: list[str] = []

        # ── 运动员画像 ──
        personal_bests = profile.get("personal_bests", {}) if profile else {}
        personal_info = profile.get("personal_info", {}) if profile else {}
        fitness_level = MemoryWriter._infer_fitness_level(personal_bests, personal_info)

        # ── 目标上下文 ──
        active_goals = goals or []
        goal_context = ""
        if active_goals:
            goal_parts = []
            for g in active_goals[:2]:
                gm = g.get("metrics", {}) if isinstance(g, dict) else getattr(g, "front_matter", {}).get("metrics", {})
                target = list(gm.values())[0] if gm else "?"
                goal_parts.append(f"目标 {target}")
            if goal_parts:
                goal_context = " | ".join(goal_parts)

        # ── 恢复分析 ──
        sleep_hours = sleep.get("total_hours", 0) or 0
        sleep_quality = sleep.get("quality", "unknown")
        rhr = morning.get("resting_hr", 50) or 50
        hrv_status = morning.get("hrv_status", "balanced") or "balanced"
        bb = morning.get("body_battery_morning", 0) or 0
        readiness = morning.get("training_readiness_score", 0) or 0

        # 睡眠评估（0 可能表示数据缺失，不一定是真没睡）
        if sleep_hours >= 8 and sleep_quality in ("excellent", "good"):
            observations.append(f"昨夜睡眠 {sleep_hours}h，质量{sleep_quality}，恢复充分")
        elif sleep_hours >= 7:
            observations.append(f"昨夜睡眠 {sleep_hours}h，基本够用但还有优化空间")
        elif sleep_hours >= 3:
            observations.append(f"昨夜仅睡 {sleep_hours}h，睡眠不足是今天最大的限制因素")
            warnings.append(f"睡眠不足会直接影响训练效果和恢复速度，今晚务必早睡")
        elif sleep_hours > 0:
            observations.append(f"昨夜睡眠 {sleep_hours}h（数据可能不完整）")
        else:
            observations.append("睡眠数据未同步（不代表没睡）")

        # HRV + RHR 联合分析
        if hrv_status in ("BALANCED", "balanced") and rhr <= 45:
            observations.append(f"HRV 状态平衡，静息心率 {rhr} bpm 处于优秀区间，自主神经系统状态良好")
        elif hrv_status in ("UNBALANCED", "unbalanced", "LOW", "low"):
            observations.append(f"HRV 处于{hrv_status}状态，静息心率 {rhr} bpm，提示身体可能处于应激状态")
            warnings.append("HRV 异常时优先保证睡眠和营养，降低训练强度")

        # 身体电量
        if bb >= 90:
            observations.append(f"晨起身体电量 {bb}%，能量储备充足")
        elif bb >= 70:
            observations.append(f"晨起身体电量 {bb}%，处于正常范围")
        elif bb > 0:
            observations.append(f"晨起身体电量仅 {bb}%，能量储备偏低")
            if sleep_hours < 7:
                recommendations.append("身体电量偏低与睡眠不足高度相关，改善睡眠是第一优先级")

        # ── 训练负荷分析 ──
        acwr = load.get("acwr", 1.0)
        acwr_status = load.get("acwr_status", "optimal")
        acute = load.get("acute_load_7d", 0)
        chronic = load.get("chronic_load_28d", 0)

        if acwr_status == "optimal":
            observations.append(f"ACWR {acwr} 处于最优区间，训练负荷合理")
        elif acwr_status == "overreaching":
            observations.append(f"ACWR {acwr} 偏高，接近过度训练边界")
            warnings.append("短期负荷上升较快，建议本周安排 1-2 天轻松训练或主动恢复")

        # ── 当日训练分析 ──
        is_rest = activity.get("is_rest_day", False)
        sessions = activity.get("sessions", [])
        total_dur = activity.get("total_duration_min", 0)
        total_dist = activity.get("total_distance_km", 0) or 0
        total_load = activity.get("total_training_load", 0)
        activity_level = activity.get("activity_level", "sedentary")

        if is_rest:
            if activity_level == "very_active":
                observations.append("昨天虽无正式训练，但全天活动量很高（步数 >20000），相当于一次中等强度有氧")
            elif activity_level == "active":
                observations.append("昨天休息日，保持了适度活动，有利于主动恢复")
            else:
                observations.append("昨天为完全休息日，身体得到了恢复")
        else:
            session_types = set(s.get("type", "") for s in sessions)
            type_desc = " + ".join(sorted(session_types))
            observations.append(
                f"昨天完成 {len(sessions)} 节训练（{type_desc}），"
                f"总计 {total_dur}min / {total_dist:.1f}km / 负荷 {total_load:.0f}"
            )
            if total_load > 200:
                observations.append("属于高强度训练日，需要充分恢复")
            elif total_load > 100:
                observations.append("属于中等强度训练日，强度适宜")
            else:
                observations.append("低强度训练日，有利于技术打磨和主动恢复")

        # 室内占比分析
        indoor = any("室内" in s.get("name", "") for s in sessions)
        outdoor = any(s.get("type") == "running" and "室内" not in s.get("name", "")
                      for s in sessions)
        if indoor and outdoor:
            observations.append("昨天兼顾了室外和室内训练，室外保持路感，室内补充跑量")

        # ── 趋势分析 ──
        if seven_day:
            # 检查睡眠趋势
            sleep_vals = [
                m.get("sleep_duration_hours", 0) or 0
                for m in seven_day if m
            ]
            if len(sleep_vals) >= 5:
                recent_avg = sum(sleep_vals[:3]) / 3 if len(sleep_vals) >= 3 else sum(sleep_vals) / len(sleep_vals)
                older_avg = sum(sleep_vals[3:]) / max(len(sleep_vals[3:]), 1)
                if older_avg - recent_avg > 0.5:
                    observations.append(f"近 3 天睡眠时长呈下降趋势（{older_avg:.1f}h → {recent_avg:.1f}h），需要关注")
                    recommendations.append("连续几天睡眠不足会累积疲劳，建议今晚设定一个早睡闹钟")

        # ── 综合恢复评分解读 ──
        rec_score = recovery.get("overall_score", 0)
        rec_level = recovery.get("level", "fair")

        if rec_level == "excellent":
            observations.append(f"综合恢复评分 {rec_score}/100，身体处于最佳状态")
        elif rec_level == "good":
            observations.append(f"综合恢复评分 {rec_score}/100，状态良好可正常训练")
        elif rec_level == "fair":
            observations.append(f"综合恢复评分 {rec_score}/100，状态一般，建议适当调整强度")

        # ── 运动员画像相关观察 ──
        if fitness_level:
            observations.append(f"运动员水平: {fitness_level}")
        if goal_context:
            observations.append(f"训练目标: {goal_context}")

        # ── 核心结论（结合画像）──
        if rec_level in ("excellent", "good") and acwr_status == "optimal":
            if fitness_level and "精英" in fitness_level:
                conclusion = f"作为{fitness_level}选手，当前状态良好。保持训练质量，关注技术细节和恢复节奏。"
            elif active_goals:
                conclusion = f"状态良好，训练负荷合理。{goal_context}——按计划推进，重点关注训练一致性。"
            else:
                conclusion = "整体状态良好，训练负荷合理，按计划执行即可。"
            confidence = "high"
        elif rec_level == "poor" or acwr_status in ("overreaching", "high_risk"):
            conclusion = "身体发出恢复不足的信号，建议今天以轻松恢复为主。今天的让步是为了明天更好的训练。"
            confidence = "high"
        elif sleep_hours < 7:
            conclusion = "除了睡眠，其他指标都还不错。今天最大的训练任务是——早睡。把睡眠补回来。"
            confidence = "medium"
        elif activity_level == "very_active" and is_rest:
            conclusion = "虽然没有正式训练，但全天活动量很高，实际上相当于完成了一次有氧。今天维持正常训练节奏即可。"
            confidence = "medium"
        else:
            conclusion = "各指标处于正常范围，可根据体感灵活调整。"
            confidence = "medium"

        return {
            "observations": observations,
            "recommendations": recommendations,
            "warnings": warnings,
            "conclusion": conclusion,
            "confidence": confidence,
        }

    # ── 辅助: 运动员水平 ──────────────────────

    @staticmethod
    def _load_profile(memory_store) -> dict[str, Any] | None:
        """从 memory store 加载运动员档案。"""
        try:
            mem = memory_store.get("fitness-assessment")
            return mem.front_matter if mem else None
        except Exception:
            return None

    @staticmethod
    def _load_active_goals(memory_store) -> list[dict[str, Any]]:
        """从 memory store 加载活跃目标。"""
        try:
            goals = memory_store.list_by_type("goal", status="active")
            return [g.front_matter for g in goals]
        except Exception:
            return []


    @staticmethod
    def _infer_fitness_level(pbs: dict[str, Any], info: dict[str, Any]) -> str:
        """根据个人最佳成绩推断运动员水平。"""
        if not pbs:
            return ""
        level = ""
        # 5K
        pb_5k = pbs.get("5k", {}).get("time", "") if isinstance(pbs.get("5k"), dict) else ""
        if pb_5k:
            parts = pb_5k.split(":")
            if len(parts) == 2:
                secs = int(parts[0]) * 60 + int(parts[1])
                if secs < 16 * 60: level = "精英"
                elif secs < 20 * 60: level = "进阶"
                elif secs < 25 * 60: level = "中级"
                else: level = "入门"
        # 10K
        if not level:
            pb_10k = pbs.get("10k", {}).get("time", "") if isinstance(pbs.get("10k"), dict) else ""
            if pb_10k:
                parts = pb_10k.split(":")
                if len(parts) == 2:
                    secs = int(parts[0]) * 60 + int(parts[1])
                    if secs < 35 * 60: level = "精英"
                    elif secs < 42 * 60: level = "进阶"
                    elif secs < 52 * 60: level = "中级"
                    else: level = "入门"
        # 半马
        if not level:
            pb_hm = pbs.get("half_marathon", {}).get("time", "") if isinstance(pbs.get("half_marathon"), dict) else ""
            if pb_hm:
                parts = pb_hm.split(":")
                if len(parts) == 2:
                    mins = int(parts[0])
                    if mins < 80: level = "精英"
                    elif mins < 95: level = "进阶"
                    elif mins < 115: level = "中级"
                    else: level = "入门"
        return f"{level}跑者" if level else ""

    # ── 辅助: 渲染 ────────────────────────────

    @staticmethod
    def _render_daily_body(fm: dict[str, Any], target_date: date) -> str:
        """渲染日报正文。"""
        ya = fm.get("yesterday_activities", {})
        sleep = fm.get("last_night_sleep", {})
        morning = fm.get("this_morning", {})
        load = fm.get("training_load", {})
        recovery = fm.get("recovery", {})
        anomalies = fm.get("anomalies", {})
        rec = fm.get("recommendation", {})

        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        wd = weekday_names[target_date.weekday()]

        lines = [
            f"# 📰 每日训练报告 — {target_date} {wd}",
            "",
            f"> 生成时间: {fm.get('generated', '')[:16]}",
            "",
            "## 🏃 今日训练",
            "",
        ]

        # 活动详情分析
        session_analyses = fm.get("session_analyses", [])
        if session_analyses:
            lines.extend(["", "## 🔬 训练细节分析", ""])
            for analysis_text in session_analyses:
                lines.append(analysis_text)

        if ya.get("is_rest_day"):
            activity_level = ya.get("activity_level", "sedentary")
            steps = ya.get("daily_steps", 0)
            dist = ya.get("daily_distance_km", 0)
            active_cal = ya.get("daily_active_cal", 0)
            level_labels = {
                "very_active": "高活跃",
                "active": "活跃",
                "light": "轻度活动",
                "sedentary": "久坐",
            }
            level_label = level_labels.get(activity_level, "—")
            lines.append(f"**休息日** — 无正式训练记录。")
            lines.append(f"全天活动: {steps} 步 | {dist} km | 活动消耗 {active_cal} cal | {level_label}")
        elif ya.get("sessions"):
            for s in ya["sessions"]:
                dist_str = f"{s['distance_km']}km" if s.get("distance_km") else ""
                hr_str = f"心率 {s['avg_hr']}" if s.get("avg_hr") else ""
                lines.append(
                    f"- **{s['type']}**: {s.get('name', '')} "
                    f"{s['duration_min']}min {dist_str} {hr_str}"
                )
            lines.append(f"\n**总时长**: {ya.get('total_duration_min', 0)}min")
            lines.append(f"**总负荷**: {ya.get('total_training_load', 0)}")

        lines.extend([
            "",
            "## 😴 睡眠恢复",
            "",
            f"- **睡眠时长**: {sleep.get('total_hours', '?')}h | 评分 {sleep.get('sleep_score', '?')} ({sleep.get('quality', '?')})",
            "",
            "## 📊 今晨状态",
            "",
            f"| 指标 | 数值 |",
            f"|------|------|",
            f"| 静息心率 | {morning.get('resting_hr', '?')} bpm |",
            f"| HRV | {morning.get('hrv_ms', '?')} ms |",
            f"| 身体电量 | {morning.get('body_battery_morning', '?')} |",
            f"| 训练准备 | {morning.get('training_readiness_score', '?')} |",
            "",
            "## 📈 负荷状态",
            "",
            f"- **ACWR**: {load.get('acwr', '?')} — {load.get('acwr_status', '?')}",
            f"- **恢复评分**: {recovery.get('overall_score', '?')}/100 ({recovery.get('level', '?')})",
            "",
            "## 🎯 今日训练建议",
            "",
            f"**{rec.get('training_advice', '?')}** (强度: {rec.get('intensity', '?')})",
        ])

        if rec.get("caution"):
            lines.append("\n⚠️ 注意事项:")
            for c in rec["caution"]:
                lines.append(f"- {c}")

        if anomalies.get("items"):
            lines.extend([
                "",
                "## ⚠️ 异常提醒",
            ])
            for item in anomalies["items"]:
                lines.append(f"- [{item['severity']}] {item['message']}")

        # AI 教练洞察
        ai = fm.get("ai_insight", {})
        if ai:
            lines.extend(["", "## 🤖 AI 教练洞察", ""])
            lines.append(f"> {ai.get('conclusion', '')}")
            lines.append("")
            if ai.get("observations"):
                lines.append("### 观察")
                for obs in ai["observations"]:
                    lines.append(f"- {obs}")
            if ai.get("warnings"):
                lines.append("\n### ⚠️ 注意")
                for w in ai["warnings"]:
                    lines.append(f"- {w}")
            if ai.get("recommendations"):
                lines.append("\n### 建议")
                for r in ai["recommendations"]:
                    lines.append(f"- {r}")

        lines.extend([
            "",
            "---",
            f"*本报告由 rundown daily 自动生成*",
        ])

        return "\n".join(lines)

    @staticmethod
    def _render_summary_body(fm: dict[str, Any], period: str) -> str:
        """渲染摘要正文。"""
        stats = fm.get("stats", {})
        period_name = "周" if period == "weekly" else "月"

        lines = [
            f"# {period_name}运动摘要",
            "",
            f"**{fm.get('start_date', '?')} ~ {fm.get('end_date', '?')}**",
            "",
            f"## 概览",
            f"- 活动次数: {stats.get('total_activities', 0)}",
            f"- 总时长: {stats.get('total_duration_min', 0)} min",
            f"- 总距离: {stats.get('total_distance_km', 0)} km",
            "",
            "---",
            f"*自动生成于 {fm.get('generated', '')}*",
        ]
        return "\n".join(lines)

    @staticmethod
    def _render_recovery_body(fm: dict[str, Any]) -> str:
        """渲染恢复摘要正文。"""
        lines = [
            "# 恢复评估",
            "",
            f"**{fm.get('start_date', '?')} ~ {fm.get('end_date', '?')}**",
            "",
            "## 综合恢复评分",
            f"**{fm.get('recovery_score', {}).get('overall_score', '?')}/100**",
            "",
            "---",
            f"*自动生成于 {fm.get('generated', '')}*",
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# MemoryValidator
# ═══════════════════════════════════════════════════════════════


class MemoryValidator:
    """记忆校验器 — Front Matter schema 验证 + 完整性检查。"""

    REQUIRED_FIELDS: dict[MemoryType, list[str]] = {
        MemoryType.DAILY_REPORT: ["type", "date", "generated"],
        MemoryType.ACTIVITY_SUMMARY: ["type", "period", "start_date", "end_date"],
        MemoryType.RECOVERY_SUMMARY: ["type", "period", "start_date"],
        MemoryType.GOAL: ["type", "goal_type", "status", "created"],
        MemoryType.TRAINING_PLAN: ["type", "start_date", "end_date", "status"],
    }

    def __init__(self, memory_dir: str):
        self._reader = MemoryReader(memory_dir)
        self._root = Path(memory_dir)

    def validate(self, memory: Memory) -> list[str]:
        """校验单条记忆的 schema，返回错误列表。"""
        errors = []
        required = self.REQUIRED_FIELDS.get(memory.type, ["type"])

        for field in required:
            if field not in memory.front_matter:
                errors.append(f"缺少必填字段: {field}")

        # 日期格式检查
        for date_field in ["date", "created", "start_date", "end_date", "updated"]:
            val = memory.front_matter.get(date_field)
            if val and isinstance(val, str):
                try:
                    date.fromisoformat(val[:10])
                except ValueError:
                    errors.append(f"日期格式错误 ({date_field}): {val}")

        return errors

    def integrity_check(self) -> dict[str, Any]:
        """全库完整性检查。

        Returns:
            {pass_count, warn_count, error_count, issues: [...]}
        """
        issues: list[dict[str, str]] = []
        checked = 0

        for path in sorted(self._root.rglob("*.md")):
            if path.parent.name == "archive":
                continue
            mem = Memory.from_file(path)
            if mem is None:
                issues.append({
                    "level": "error",
                    "file": str(path.relative_to(self._root)),
                    "message": "无法解析记忆文件",
                })
                continue

            checked += 1
            errors = self.validate(mem)
            for e in errors:
                issues.append({
                    "level": "error",
                    "file": str(path.relative_to(self._root)),
                    "message": e,
                })

        pass_count = checked - len([i for i in issues if i["level"] == "error"])
        warn_count = len([i for i in issues if i["level"] == "warning"])
        error_count = len([i for i in issues if i["level"] == "error"])

        return {
            "checked": checked,
            "pass_count": pass_count,
            "warn_count": warn_count,
            "error_count": error_count,
            "issues": issues,
        }


# ═══════════════════════════════════════════════════════════════
# MemoryStore (Facade)
# ═══════════════════════════════════════════════════════════════


class MemoryStore:
    """记忆存储系统门面。

    组合 MemoryReader、MemoryWriter、MemoryValidator，
    提供统一的记忆管理接口。
    """

    def __init__(
        self,
        memory_dir: str,
        db_getter: Callable[[], Any] | None = None,
        api_client_getter: Callable[[], Any] | None = None,
    ):
        self.reader = MemoryReader(memory_dir)
        self.writer = MemoryWriter(memory_dir, db_getter, api_client_getter, memory_store=self)
        self.validator = MemoryValidator(memory_dir)

    # 委托 Reader
    def get(self, memory_id: str) -> Memory | None:
        return self.reader.get(memory_id)

    def list_by_type(self, memory_type: MemoryType, **filters: Any) -> list[Memory]:
        return self.reader.list_by_type(memory_type, **filters)

    def get_latest(self, memory_type: MemoryType) -> Memory | None:
        return self.reader.get_latest(memory_type)

    def query(self, tags: list[str] | None = None,
              date_range: tuple[date, date] | None = None) -> list[Memory]:
        return self.reader.query(tags, date_range)

    def search(self, keyword: str) -> list[Memory]:
        return self.reader.search(keyword)

    def get_index(self, category: str) -> Memory | None:
        return self.reader.get_index(category)

    # 委托 Writer
    def generate_daily_report(self, user_id: str,
                               target_date: date | None = None) -> Memory:
        return self.writer.generate_daily_report(user_id, target_date)

    def generate_weekly_summary(self, user_id: str,
                                 target_date: date | None = None) -> Memory:
        return self.writer.generate_weekly_summary(user_id, target_date)

    def generate_recovery_summary(self, user_id: str,
                                   target_date: date | None = None) -> Memory:
        return self.writer.generate_recovery_summary(user_id, target_date)

    def rebuild_index(self, category: str) -> None:
        return self.writer.rebuild_index(category)

    # 委托 Validator
    def validate(self, memory: Memory) -> list[str]:
        return self.validator.validate(memory)

    def integrity_check(self) -> dict[str, Any]:
        return self.validator.integrity_check()

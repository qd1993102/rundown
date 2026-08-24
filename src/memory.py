"""记忆存储模块 — 在运动数据之上构建结构化运动知识库。

核心设计：
- 数据层 (SQLite) 存储"事实"，记忆层 (Markdown + YAML FM) 存储"认知"
- 自动生成：每日报告、运动摘要、恢复摘要、执行跟踪
- 人工维护：竞技档案、目标管理、训练计划、教练知识
- AI 原生：文件格式天然适合作为 LLM 上下文
"""

from __future__ import annotations

import copy
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import yaml

from .local_files import atomic_write_private
from .training_analysis import (
    ACTIVITY_SYNC_METRIC_TYPE,
    ActivityDayState,
    _first_number,
    natural_week_bounds,
)
from .training_day_summary import TrainingDaySummaryBuilder
from .training_planning import ensure_training_prescription, workout_steps_summary

logger = logging.getLogger(__name__)

_MEMORY_FILE_CACHE: dict[str, tuple[int, int, "Memory"]] = {}
_MEMORY_FILE_CACHE_MAX = 4096
_MEMORY_FILE_CACHE_GUARD = threading.Lock()


def load_platform_thresholds(
    db: Any, user_id: int, target_date: date,
) -> dict[str, int]:
    """读取平台自算乳酸阈值（daily_health_metrics.lthr/ltsp）。

    Coros /analyse/query 按日返回 lthr（阈值心率 bpm）与 ltsp（阈值配速 s/km）；
    取目标日期当天或之前最近一条有效值；列不存在或查询失败时返回空（不猜测）。
    """
    try:
        from sqlalchemy import text
        session = db.get_session()
        try:
            row = session.execute(text("""
                SELECT lthr, ltsp FROM daily_health_metrics
                WHERE user_id = :uid AND metric_date <= :md
                  AND (lthr IS NOT NULL OR ltsp IS NOT NULL)
                ORDER BY metric_date DESC LIMIT 1
            """), {"uid": user_id, "md": str(target_date)}).fetchone()
        finally:
            session.close()
    except Exception:
        return {}
    if row is None:
        return {}
    result: dict[str, int] = {}
    if row.lthr:
        result["threshold_heart_rate"] = int(row.lthr)
    if row.ltsp:
        result["threshold_pace_sec_per_km"] = int(row.ltsp)
    return result


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
    fm_raw = match.group(1)
    fm = _parse_yaml_safe(fm_raw)
    body = text[match.end():]
    return fm, body


def _parse_yaml_safe(yaml_text: str) -> dict[str, Any]:
    """安全解析 YAML，兼容残留的 !!python/tuple 标签。

    优先使用 safe_load；若失败则回退到 unsafe_load 并转换 tuple->list。
    """
    try:
        return yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        try:
            fm = yaml.load(yaml_text, Loader=yaml.Loader) or {}
            return _sanitize_for_yaml(fm)
        except yaml.YAMLError:
            return {}


def get_daily_activities(front_matter: dict[str, Any]) -> dict[str, Any]:
    """读取报告日活动，兼容旧日报的 ``yesterday_activities`` 字段。"""

    current = front_matter.get("daily_activities")
    if isinstance(current, dict):
        return current
    legacy = front_matter.get("yesterday_activities")
    return legacy if isinstance(legacy, dict) else {}


def build_memory_file(front_matter: dict[str, Any], body: str) -> str:
    """构建带 YAML Front Matter 的 Markdown 内容。

    使用 safe_dump 并将所有 tuple 转为 list，避免产生 !!python/tuple 标签，
    确保 safe_load 可正常解析。
    """
    fm_yaml = yaml.safe_dump(
        _sanitize_for_yaml(front_matter),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    return f"---\n{fm_yaml}\n---\n\n{body}".strip() + "\n"


def _sanitize_for_yaml(obj: Any) -> Any:
    """递归将 tuple 转为 list，确保 safe_dump 不会产生 !!python/tuple 标签。"""
    if isinstance(obj, tuple):
        return [_sanitize_for_yaml(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_for_yaml(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_yaml(item) for item in obj]
    return obj


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
        """从文件加载记忆，并按文件签名复用解析结果。"""
        key = str(path)
        try:
            stat = path.stat()
        except FileNotFoundError:
            with _MEMORY_FILE_CACHE_GUARD:
                _MEMORY_FILE_CACHE.pop(key, None)
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        with _MEMORY_FILE_CACHE_GUARD:
            cached = _MEMORY_FILE_CACHE.get(key)
            if cached and cached[:2] == signature:
                return copy.deepcopy(cached[2])
        try:
            text = path.read_text(encoding="utf-8")
            fm, body = parse_front_matter(text)
            memory_id = fm.get("id", path.stem)
            memory_type = MemoryType(fm.get("type", "activity_summary"))
            result = cls(
                id=memory_id,
                type=memory_type,
                path=path,
                front_matter=fm,
                body=body,
            )
            with _MEMORY_FILE_CACHE_GUARD:
                _MEMORY_FILE_CACHE[key] = (signature[0], signature[1], copy.deepcopy(result))
                while len(_MEMORY_FILE_CACHE) > _MEMORY_FILE_CACHE_MAX:
                    _MEMORY_FILE_CACHE.pop(next(iter(_MEMORY_FILE_CACHE)))
            return result
        except Exception as exc:
            logger.warning("加载记忆文件失败 %s: %s", path, exc)
            return None

    def save(self) -> None:
        """保存记忆到文件，并驱逐旧的解析缓存。"""
        content = build_memory_file(self.front_matter, self.body)
        with _MEMORY_FILE_CACHE_GUARD:
            _MEMORY_FILE_CACHE.pop(str(self.path), None)
        atomic_write_private(self.path, content)

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
        *,
        readiness: dict[str, Any],
        athlete_context: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> Memory:
        """生成每日综合报告。

        从 SQLite 查询：
        - 今日活动（当天训练数据）
        - 昨夜睡眠（前一天晚上的恢复数据）
        - 今晨状态指标
        - 7 日趋势
        - 异常检测
        - 训练建议

        ``athlete_context`` 由编排层按门禁调用训练域只读能力画像后传入；
        写入器只负责落盘，不自行读取训练域。

        Args:
            user_id: 运动平台用户 ID。
            target_date: 报告日期，默认今天。

        Returns:
            生成的 Memory 对象。
        """
        if target_date is None:
            target_date = date.today()

        readiness_status = str(readiness.get("status") or "")
        if readiness_status not in {"ready", "limited"}:
            raise RuntimeError("日报生成缺少已通过的数据完整性门禁")
        omitted_sections = {
            str(item) for item in readiness.get("omitted_sections", [])
        }

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

        # 5. 查询近 7/28 天活动用于 ACWR（均包含报告日期）
        seven_day_activities = self._safe_get_activities(
            db, user_id, target_date - timedelta(days=6), target_date,
        )
        twenty_eight_day_activities = self._safe_get_activities(
            db, user_id, target_date - timedelta(days=27), target_date,
        )

        # 使用原始活动、分段与个人基线生成版本化训练内容分析。
        session_analyses = self._analyze_training_sessions(
            db, int(user_id), activities, twenty_eight_day_activities,
            health_data or {},
            profile=(
                MemoryWriter._load_profile(self._memory_store)
                if self._memory_store else None
            ),
        )

        # ── 先汇总各维度（供 Front Matter 和 recommendation 共用） ──
        activity_state = self._get_activity_day_state(
            db, int(user_id), target_date, activities,
        )
        activity_summary = self._summarize_activities(
            activities, today_health, activity_state=activity_state,
        )
        week_start, _ = natural_week_bounds(target_date)
        week_activities = self._safe_get_activities(
            db, user_id, week_start, target_date,
        )
        plan_context = self._load_plan_context(target_date)
        sleep_summary = (
            self._unavailable_section("睡眠数据不完整")
            if "sleep" in omitted_sections
            else self._summarize_sleep(sleep)
        )
        morning_summary = (
            self._unavailable_section("恢复指标不完整")
            if "recovery" in omitted_sections
            else self._summarize_morning(morning)
        )

        training_load = (
            self._unavailable_section("活动历史覆盖不足，未计算 ACWR")
            if "training_load" in omitted_sections
            else self._calc_training_load(
                seven_day_activities, twenty_eight_day_activities,
            )
        )
        recovery = (
            self._unavailable_section("睡眠或恢复指标不完整，未计算恢复评分")
            if "recovery" in omitted_sections
            else self._calc_recovery_score(sleep, morning)
        )
        trends = (
            self._unavailable_section("近 7 天健康数据覆盖不足")
            if "trends_7d" in omitted_sections
            else self._calc_trends(seven_day_metrics)
        )
        anomalies = (
            {"status": "unavailable", "items": [], "reason": "健康趋势数据不足"}
            if "anomalies" in omitted_sections
            else self._detect_anomalies(seven_day_metrics)
        )
        training_day_summary = TrainingDaySummaryBuilder.prepare_daily_analysis(
            TrainingDaySummaryBuilder.build(
                target=target_date,
                activities=activities,
                states={str(target_date): "synced"} if readiness_status == "ready" else {},
                sleep=sleep_summary,
                recovery=recovery,
                training_load=training_load,
                plan_context=plan_context,
                finality=readiness.get("finality", "provisional"),
                facts_cutoff=readiness.get("data_as_of") or target_date,
            )
        )
        recommendation = (
            {
                "status": "unavailable",
                "ready_to_train": None,
                "intensity": "unknown",
                "training_advice": "数据不足，暂不提供训练强度建议",
                "caution": ["请先补齐页面列出的数据维度后重新生成完整日报"],
            }
            if "recommendation" in omitted_sections
            else self._generate_recommendation(
                sleep_summary, morning_summary, seven_day_activities,
                twenty_eight_day_activities, session_analyses,
            )
        )
        plan_execution_summary = self._build_plan_execution_summary(
            plan_context, activity_summary, week_activities, target_date,
            recommendation,
        )

        # ── 训练域能力画像背景（受限版或缺载时显式不可用，不阻断生成） ──
        if "training_load" in omitted_sections:
            athlete_context = {
                "status": "unavailable",
                "reason": "training_load_omitted",
                "source": "training_domain_capacity_profile",
            }
        elif athlete_context is None:
            athlete_context = {
                "status": "unavailable",
                "reason": "not_loaded",
                "source": "training_domain_capacity_profile",
            }

        # ── 构建 Front Matter ──
        fm: dict[str, Any] = {
            "type": "daily_report",
            "date": str(target_date),
            "generated": datetime.now().isoformat(timespec="seconds"),
            "version": 1,
            "data_readiness": (
                "complete" if readiness_status == "ready" else "limited"
            ),
            "report_finality": readiness.get("finality", "provisional"),
            "data_as_of": readiness.get("data_as_of"),
            "data_coverage": readiness.get("dimensions", {}),
            "omitted_sections": sorted(omitted_sections),
            # 当日活动；旧字段保留为只读客户端的兼容别名。
            "daily_activities": activity_summary,
            "yesterday_activities": copy.deepcopy(activity_summary),
            # 昨夜睡眠
            "last_night_sleep": sleep_summary,
            # 今晨状态
            "this_morning": morning_summary,
            # 训练负荷
            "training_load": training_load,
            # 恢复评分
            "recovery": recovery,
            # 训练方案的历史快照与面向报告的执行解释。
            "plan_context": plan_context,
            "plan_execution_summary": plan_execution_summary,
            # 7 日趋势
            "trends_7d": trends,
            # 异常检测
            "anomalies": anomalies,
            # 报告日建议（使用汇总后的数据）
            "recommendation": recommendation,
            # 标签
            "tags": self._build_tags(activities, target_date),
            # 版本化训练内容识别
            "session_analyses": session_analyses,
            # 日报、周报和草稿共享的确定性单日摘要。
            "training_day_summary": training_day_summary,
            # 训练域只读能力画像背景（草稿同口径），供正文与 AI 洞察引用。
            "athlete_context": athlete_context,
            # ── 确定性跑步分析（S9/S10/S11/S13） ──
            "running_analysis_daily": MemoryWriter._build_running_analysis_daily(
                session_analyses, training_load, recovery, today_health,
                seven_day_metrics, seven_day_activities, twenty_eight_day_activities,
            ),
        }

        # 10. AI 教练洞察
        # 受限版同样生成洞察：调用方提供在线洞察时直接采用；未提供时走确定性兜底，
        # 兜底按 omitted_sections 跳过缺失维度结论，避免用户有跑步时结论整块空白。
        if ai_insight is not None:
            fm["ai_insight"] = ai_insight
        else:
            profile = MemoryWriter._load_profile(self._memory_store) if self._memory_store else None
            goals = MemoryWriter._load_active_goals(self) if self._memory_store else None
            ai_insight = self._generate_ai_insight(
                activity_summary, sleep_summary, morning_summary,
                fm["training_load"], fm["recovery"], fm["anomalies"],
                seven_day_metrics,
                profile=profile, goals=goals,
                session_analyses=session_analyses,
                omitted_sections=sorted(omitted_sections),
                running_analysis_daily=fm.get("running_analysis_daily"),
            )
            fm["ai_insight"] = ai_insight

        file_path = self._root / "auto" / "daily" / f"{target_date}.md"
        memory = Memory(
            id=str(target_date),
            type=MemoryType.DAILY_REPORT,
            path=file_path,
            front_matter=fm,
            body="",
        )
        if persist:
            return self.finalize_daily_report(memory)
        return memory

    def finalize_daily_report(
        self,
        memory: Memory,
        ai_insight: dict[str, Any] | None = None,
    ) -> Memory:
        """将已构建的日报事实与最终洞察组装后只写入一次。"""

        if memory.type != MemoryType.DAILY_REPORT:
            raise ValueError("只能完成 daily_report")
        if ai_insight is not None:
            memory.front_matter["ai_insight"] = ai_insight
        target_date = date.fromisoformat(str(memory.front_matter["date"]))
        memory.body = self._render_daily_body(memory.front_matter, target_date)
        memory.path.parent.mkdir(parents=True, exist_ok=True)
        memory.save()
        logger.info("📰 日报已生成: %s", memory.path)
        return memory

    @staticmethod
    def _unavailable_section(reason: str) -> dict[str, Any]:
        return {"status": "unavailable", "reason": reason}

    # ── 周/月摘要 ──────────────────────────────

    def generate_weekly_summary(
        self, user_id: str, target_date: date | None = None,
    ) -> Memory:
        """生成周运动摘要。"""
        if target_date is None:
            target_date = date.today()

        monday, sunday = natural_week_bounds(target_date)
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

        monday, sunday = natural_week_bounds(target_date)
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
        atomic_write_private(index_path, content)
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
            try:
                rows = session.execute(text("""
                    SELECT user_id, activity_id, activity_date, activity_name,
                           duration_seconds, avg_heart_rate, training_load,
                           start_time, distance_meters, activity_type,
                           max_heart_rate, calories, elevation_gain,
                           provider_name, created_at
                    FROM activities
                    WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                    ORDER BY start_time
                """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            finally:
                session.close()
            return [
                {
                    "user_id": r[0], "activity_id": r[1], "activity_date": r[2],
                    "activity_name": r[3], "duration_seconds": r[4],
                    "avg_heart_rate": r[5], "training_load": r[6],
                    "start_time": r[7], "distance_meters": r[8],
                    "activity_type": r[9], "max_heart_rate": r[10],
                    "calories": r[11], "elevation_gain": r[12],
                    "provider_name": r[13], "created_at": r[14],
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
            try:
                rows = session.execute(text("""
                    SELECT user_id, activity_id, activity_date, activity_name,
                           duration_seconds, avg_heart_rate, training_load,
                           start_time, created_at
                    FROM activities
                    WHERE user_id = :uid AND activity_date >= :start AND activity_date <= :end
                    ORDER BY start_time
                """), {"uid": user_id, "start": str(start), "end": str(end)}).fetchall()
            finally:
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

    @staticmethod
    def _segment_sequence_fallback(
        session_summary: dict[str, Any],
        splits: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """结构判定的分段序列来源：优先用 summary 的 segment_sequence。

        存量活动（旧 schema summary 无 segment_sequence/quantity_gate）时从
        activity_splits 重建分段特征序列，并按分段距离合计 vs summary 距离
        计算量特征门禁——Garmin splitSummaries 的 ×2 距离异常会被标记为
        仅强度模式（quantity_reliable=False），不输出段距离/时长结论。
        """
        segment_sequence = session_summary.get("segment_sequence") or []
        quantity_gate = session_summary.get("quantity_gate") or {}
        quantity_reliable = bool(quantity_gate.get("quantity_reliable", True))
        if not segment_sequence and splits:
            summary_distance = (
                session_summary.get("volume") or {}
            ).get("distance_m")
            total_split_distance = sum(
                float(s.get("distance_m") or 0) for s in splits
            )
            if summary_distance and total_split_distance > 0:
                ratio = total_split_distance / float(summary_distance)
                quantity_reliable = 0.85 <= ratio <= 1.15
            segment_sequence = [
                {
                    "pace_sec_per_km": s.get("pace_per_km"),
                    "avg_hr": s.get("avg_hr"),
                    "avg_cadence": s.get("avg_cadence"),
                    "stride_length_cm": s.get("stride_length_cm"),
                    "duration_s": s.get("duration_sec"),
                    "split_type": s.get("type") or s.get("split_type"),
                }
                for s in splits
            ]
        return segment_sequence, quantity_reliable

    @staticmethod
    def _build_running_analysis_daily(
        session_analyses: list[dict[str, Any]],
        training_load: dict[str, Any],
        recovery: dict[str, Any],
        today_health: dict[str, Any] | None,
        seven_day_metrics: list[dict[str, Any]],
        seven_day_activities: list[dict[str, Any]],
        twenty_eight_day_activities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """构建日报级别的确定性分析结果（S9/S10/S11/S13）。"""
        try:
            from .running_analysis import (
                analyze_hrv_baseline,
                check_recovery_performance_consistency,
                assess_injury_risk,
            )
        except Exception:
            return {"status": "unavailable", "reason": "import_error"}

        result: dict[str, Any] = {"status": "ok"}

        # ── S9: HRV 基线 ──
        try:
            health = today_health or {}
            recent_health = seven_day_metrics or []
            hrv_result = analyze_hrv_baseline(
                {
                    "hrv_last_night_avg": health.get("hrv_last_night_avg"),
                    "hrv_weekly_avg": health.get("hrv_weekly_avg"),
                    "resting_heart_rate": health.get("resting_heart_rate"),
                    "sleep_duration_hours": health.get("sleep_duration_hours"),
                    "body_battery_high": health.get("body_battery_high"),
                    "training_readiness_score": health.get("training_readiness_score"),
                },
                historical=[
                    {
                        "hrv_last_night_avg": m.get("hrv_last_night_avg"),
                        "resting_heart_rate": m.get("resting_heart_rate"),
                        "sleep_duration_hours": m.get("sleep_duration_hours"),
                    }
                    for m in recent_health
                ],
            )
            from dataclasses import asdict
            result["hrv_baseline"] = asdict(hrv_result)
        except Exception as exc:
            result["hrv_baseline"] = {"status": "error", "reason": str(exc)}

        # ── S10: 状态-表现一致性 ──
        try:
            # 从 per-session 分析中提取漂移和经济性
            drift = None
            economy = None
            for sa in (session_analyses or []):
                ra = sa.get("running_analysis")
                if ra:
                    if ra.get("aerobic_drift", {}).get("status") == "ok":
                        from .running_analysis import AerobicDriftResult
                        d = ra["aerobic_drift"]
                        drift = AerobicDriftResult(**{k: v for k, v in d.items() if k != "status" or True})
                    if ra.get("economy", {}).get("status") == "ok":
                        from .running_analysis import EconomyResult
                        e = ra["economy"]
                        economy = EconomyResult(**{k: v for k, v in e.items() if k != "status" or True})
                    if drift or economy:
                        break

            if hrv_result.status == "ok":
                consistency = check_recovery_performance_consistency(
                    hrv=hrv_result, drift=drift, economy=economy,
                )
                result["consistency"] = asdict(consistency)
            else:
                result["consistency"] = {"status": "insufficient_data",
                                          "note": "HRV 数据不可用"}
        except Exception as exc:
            result["consistency"] = {"status": "error", "reason": str(exc)}

        # ── S11: ACWR ──
        try:
            # 与日报展示使用同一份 7/28 天活动窗口和“28 天周均”口径。
            # calculate_acwr 的日均口径适合独立分析，但不能和日报的周均口径混用。
            result["acwr"] = {
                "status": "ok" if training_load.get("acwr") is not None else "insufficient_data",
                "acute_load": training_load.get("acute_load_7d"),
                "chronic_load": training_load.get("chronic_load_28d"),
                "acwr": training_load.get("acwr"),
                "risk_level": {
                    "undertraining": "减量区",
                    "optimal": "安全区",
                    "overreaching": "警戒区",
                    "high_risk": "高风险区",
                }.get(training_load.get("acwr_status")),
                "risk_label": training_load.get("acwr_status"),
                "acute_days": len({str(a.get("activity_date") or a.get("date")) for a in seven_day_activities}),
                "chronic_days": len({str(a.get("activity_date") or a.get("date")) for a in twenty_eight_day_activities}),
                "note": "与日报训练负荷使用相同窗口和计算口径",
            }
        except Exception as exc:
            result["acwr"] = {"status": "error", "reason": str(exc)}

        # ── S13: 伤病风险 ──
        try:
            # 收集近期疲劳代偿模式
            fatigue_patterns = []
            for sa in (session_analyses or []):
                ra = sa.get("running_analysis")
                if ra and ra.get("fatigue_compensation", {}).get("status") == "ok":
                    fp = ra["fatigue_compensation"]
                    fatigue_patterns.append(fp.get("pattern"))

            from .running_analysis import FatigueCompensationResult
            today_fatigue = None
            for sa in (session_analyses or []):
                ra = sa.get("running_analysis")
                if ra and ra.get("fatigue_compensation", {}).get("status") == "ok":
                    fp = ra["fatigue_compensation"]
                    today_fatigue = FatigueCompensationResult(
                        status=fp.get("status", "ok"),
                        pattern=fp.get("pattern"),
                        stride_change_pct=fp.get("stride_change_pct"),
                        cadence_cv_change=fp.get("cadence_cv_change"),
                    )
                    break

            acwr_data = result.get("acwr", {})
            if acwr_data.get("status") == "ok":
                from .running_analysis import AcwrResult
                acwr_obj = AcwrResult(
                    status=acwr_data["status"],
                    acwr=acwr_data.get("acwr"),
                    risk_level=acwr_data.get("risk_level"),
                )
            else:
                acwr_obj = None

            injury = assess_injury_risk(
                acwr=acwr_obj,
                hrv=hrv_result if hrv_result.status == "ok" else None,
                fatigue=today_fatigue,
                recent_fatigue_patterns=fatigue_patterns,
            )
            result["injury_risk"] = asdict(injury)
        except Exception as exc:
            result["injury_risk"] = {"status": "error", "reason": str(exc)}

        return result

    @staticmethod
    def _analyze_training_sessions(
        db: Any,
        user_id: int,
        activities: list[dict[str, Any]],
        baseline_activities: list[dict[str, Any]],
        recovery: dict[str, Any],
        profile: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """分析并持久化当日活动，失败时不阻断日报生成。"""
        from .activity import get_activity_splits, get_activity_summary_facts
        from .training_analysis import (
            ActivityFactsNormalizer,
            AthleteBaselineBuilder,
            TrainingSessionAnalyzer,
            _is_running,
            display_name,
            save_training_analysis,
        )

        analyzer = TrainingSessionAnalyzer()
        normalizer = ActivityFactsNormalizer()
        baseline_builder = AthleteBaselineBuilder()
        results = []
        for activity in activities:
            activity_id = str(activity.get("activity_id") or "")
            if not activity_id:
                continue
            try:
                try:
                    splits = get_activity_splits(db, activity_id)
                except Exception:
                    splits = []
                target_time = (
                    activity.get("start_time")
                    or activity.get("activity_date")
                )
                merged_profile = MemoryWriter._merge_platform_thresholds(
                    profile, db, user_id, target_time,
                )
                baseline = baseline_builder.build(
                    baseline_activities,
                    target_time=target_time,
                    profile=merged_profile,
                )
                analysis = analyzer.analyze(
                    activity, splits, baseline, recovery,
                )
                facts = normalizer.normalize(activity, splits)
                analysis_id = save_training_analysis(
                    db, user_id, facts, baseline, analysis,
                )
                result = {
                    "analysis_id": analysis_id,
                    "activity_id": activity_id,
                    "activity_name": activity.get("activity_name", ""),
                    # 运动模态与训练课型是两个独立事实。即使课型因时长或
                    # 强度证据不足而为 unknown，也必须保留“这是跑步”。
                    "is_running": _is_running(activity),
                    "display_name": display_name(
                        analysis.primary_type, analysis.terrain,
                    ),
                    **analysis.to_dict(),
                }
                # Reports consume the compact, versioned aggregate.  Keep raw
                # detail JSON confined to the activity storage boundary.
                try:
                    session_summary = get_activity_summary_facts(db, activity_id)
                except Exception:
                    session_summary = None
                if session_summary:
                    result["session_summary"] = session_summary
                    activity["session_summary"] = session_summary
                    basis = (session_summary.get("intensity") or {}).get("basis")
                    if basis:
                        result["evidence"] = list(result.get("evidence") or ()) + [
                            f"summary intensity basis={basis}"
                        ]
                    # 训练效果：Provider 原始 TE 缺失且个人阈值可用时，本地估算并显式标记。
                    effect = session_summary.get("effect") or {}
                    has_original_te = bool(
                        effect.get("aerobic_training_effect")
                        or effect.get("anaerobic_training_effect")
                    )
                    if not has_original_te:
                        from .summary_extraction import estimate_training_effect

                        estimated = estimate_training_effect(
                            splits,
                            threshold_heart_rate=baseline.threshold_heart_rate,
                            threshold_pace_sec_per_km=baseline.threshold_pace_sec_per_km,
                        )
                        if estimated:
                            session_summary = {
                                **session_summary, "effect": estimated,
                            }
                            result["session_summary"] = session_summary
                            activity["session_summary"] = session_summary
                    # 训练结构：加权确定性判定（配速+心率主证据，步频/步幅辅助），
                    # 需要个人阈值，分析时实时计算，不固化入库。
                    try:
                        from .summary_extraction import classify_training_structure

                        segment_sequence, quantity_reliable = (
                            MemoryWriter._segment_sequence_fallback(
                                session_summary, splits,
                            )
                        )
                        classification = classify_training_structure(
                            segment_sequence,
                            threshold_heart_rate=baseline.threshold_heart_rate,
                            threshold_pace_sec_per_km=baseline.threshold_pace_sec_per_km,
                            quantity_reliable=quantity_reliable,
                            activity_name=activity.get("activity_name") or "",
                        )
                        result["structure_classification"] = classification
                    except Exception as exc:
                        logger.warning(
                            "训练结构判定失败 activity_id=%s error_type=%s",
                            activity_id, type(exc).__name__,
                        )
                    # ── 确定性跑步分析（S1/S4/S5/S6/S7/S8） ──
                    try:
                        from .running_analysis import (
                            clean_segment_sequence,
                            analyze_aerobic_drift,
                            assess_running_economy,
                            identify_fatigue_compensation,
                            compensate_environment,
                            analyze_cardiac_muscle_decoupling,
                        )
                        from dataclasses import asdict
                        segs, _ = MemoryWriter._segment_sequence_fallback(
                            session_summary, splits,
                        )
                        cleaning = clean_segment_sequence(segs)
                        drift = analyze_aerobic_drift(cleaning.segments,
                                                       summary=session_summary)
                        economy = assess_running_economy(cleaning.segments,
                                                          summary=session_summary,
                                                          baseline=baseline)
                        fatigue = identify_fatigue_compensation(cleaning.segments,
                                                                  summary=session_summary,
                                                                  baseline=baseline)
                        env = compensate_environment(
                            avg_pace_sec_per_km=(
                                (session_summary.get("pace_profile") or {}).get("avg_pace_sec_per_km")
                                or _first_number(activity, "avg_pace_sec_per_km")
                            ),
                            avg_hr=(
                                (session_summary.get("structure") or {}).get("avg_hr")
                                or activity.get("avg_heart_rate")
                            ),
                            total_ascent_m=(
                                (session_summary.get("elevation_profile") or {}).get("ascent_m")
                                or activity.get("elevation_gain")
                            ),
                            distance_m=(
                                (session_summary.get("volume") or {}).get("distance_m")
                                or activity.get("distance_meters")
                            ),
                            gain_per_km=(
                                (session_summary.get("elevation_profile") or {}).get("gain_per_km")
                            ),
                        )
                        decoupling = analyze_cardiac_muscle_decoupling(
                            cleaning.segments, summary=session_summary, baseline=baseline,
                        )
                        result["running_analysis"] = {
                            "cleaning": asdict(cleaning),
                            "aerobic_drift": asdict(drift),
                            "economy": asdict(economy),
                            "fatigue_compensation": asdict(fatigue),
                            "environment": asdict(env),
                            "decoupling": asdict(decoupling),
                        }
                    except Exception as exc:
                        logger.warning(
                            "跑步分析失败 activity_id=%s error_type=%s",
                            activity_id, type(exc).__name__,
                        )
                activity["training_analysis"] = result
                results.append(result)
            except Exception as exc:
                logger.warning("训练内容识别失败 activity_id=%s: %s", activity_id, exc)
        return results

    # ── 辅助: 数据聚合 ────────────────────────

    @staticmethod
    def _summarize_activities(
        activities: list[dict[str, Any]],
        health_data: dict[str, Any] | None = None,
        *,
        activity_state: ActivityDayState | str | None = None,
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
            state = (
                ActivityDayState.CONFIRMED_REST
                if activity_state == ActivityDayState.CONFIRMED_REST
                else ActivityDayState.UNKNOWN
            )
            return {
                "activity_state": state.value,
                "is_rest_day": state == ActivityDayState.CONFIRMED_REST,
                "is_training_day": False,
                "sessions": [],
                "total_sessions": 0,
                "total_duration_min": 0,
                "total_distance_km": 0,
                "total_calories": 0,
                "total_training_load": 0,
                "day_type": (
                    "rest"
                    if state == ActivityDayState.CONFIRMED_REST
                    else "unknown"
                ),
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
            stored_type = str(a.get("activity_type") or "").lower()
            if "run" in stored_type or "跑" in activity_name or "run" in activity_name.lower():
                atype = "running"
            elif "cycl" in stored_type or "骑" in activity_name or "cycling" in activity_name.lower():
                atype = "cycling"
            elif "swim" in stored_type or "游泳" in activity_name or "swim" in activity_name.lower():
                atype = "swimming"
            elif "strength" in stored_type or "力量" in activity_name or "strength" in activity_name.lower():
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
                "elevation_gain_m": a.get("elevation_gain"),
                "training_analysis": a.get("training_analysis"),
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
            "activity_state": ActivityDayState.TRAINING.value,
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
    def _get_activity_day_state(
        db: Any,
        user_id: int,
        target_date: date,
        activities: list[dict[str, Any]],
    ) -> ActivityDayState:
        """根据活动事实与同步覆盖证据解析每日运动状态。"""

        if activities:
            return ActivityDayState.TRAINING

        session = None
        try:
            from sqlalchemy import text

            session = db.get_session()
            row = session.execute(text("""
                SELECT status
                FROM sync_status
                WHERE user_id = :uid
                  AND sync_date = :sync_date
                  AND metric_type = :metric_type
                ORDER BY synced_at DESC
                LIMIT 1
            """), {
                "uid": user_id,
                "sync_date": str(target_date),
                "metric_type": ACTIVITY_SYNC_METRIC_TYPE,
            }).fetchone()
            if row and str(row[0]).lower() == "completed":
                return ActivityDayState.CONFIRMED_REST
        except Exception as exc:
            logger.debug(
                "查询活动同步覆盖失败 date=%s error_type=%s",
                target_date, type(exc).__name__,
            )
        finally:
            if session is not None:
                session.close()
        return ActivityDayState.UNKNOWN

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
        def activity_load(activity: dict[str, Any]) -> float:
            return float(
                activity.get("training_load")
                or activity.get("activity_training_load")
                or 0
            )

        acute = sum(activity_load(a) for a in recent)
        # 28 天累计负荷折算为周均，与 7 天急性负荷保持相同时间单位。
        chronic = sum(activity_load(a) for a in chronic_activities) / 4

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
            # 负荷统一保留 1 位小数：浮点累加会引入二进制精度噪声
            # （历史数据曾出现 acute_load_7d: 1000.6190490722656），
            # 展示层按整数四舍五入，避免长小数溢出。
            "acute_load_7d": round(acute, 1),
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
        hrv_score = hrv_map.get(str(hrv_status).lower(), 60)
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
        recent_activities: list[dict[str, Any]],
        chronic_activities: list[dict[str, Any]],
        session_analyses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """生成报告日训练建议。"""
        load = MemoryWriter._calc_training_load(
            recent_activities, chronic_activities,
        )
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

        follow_up_constraints = []
        for analysis in session_analyses or []:
            for implication in analysis.get("training_implications", []):
                if implication not in follow_up_constraints:
                    follow_up_constraints.append(implication)

        return {
            "ready_to_train": ready,
            "training_advice": advice,
            "intensity": intensity,
            "caution": cautions,
            "focus_areas": ["技术动作", "核心力量"],
            "follow_up_constraints": follow_up_constraints,
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
        session_analyses: list[dict[str, Any]] | None = None,
        omitted_sections: list[str] | None = None,
        running_analysis_daily: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """基于数据规则生成 AI 教练自然语言洞察。

        结合运动员画像（竞技水平、目标）给出个性化分析。
        """
        observations: list[str] = []
        recommendations: list[str] = []
        warnings: list[str] = []
        omitted = set(omitted_sections or [])
        sleep_omitted = "sleep" in omitted
        recovery_omitted = "recovery" in omitted
        load_omitted = "training_load" in omitted
        trends_omitted = "trends_7d" in omitted

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

        # ── 恢复分析（教练观察三块之一，合并为一段连贯表达，同一事实只出现一次）──
        recovery_parts: list[str] = []

        # 睡眠评估（0 可能表示数据缺失，不一定是真没睡）；睡眠维度被省略时不下结论
        if sleep_omitted:
            recovery_parts.append("睡眠数据缺失，未据此评估恢复状态")
        elif sleep_hours >= 8 and sleep_quality in ("excellent", "good"):
            recovery_parts.append(f"昨夜睡眠 {sleep_hours}h、质量{sleep_quality}，恢复充分")
        elif sleep_hours >= 7:
            recovery_parts.append(f"昨夜睡眠 {sleep_hours}h，基本够用但还有优化空间")
        elif sleep_hours >= 3:
            recovery_parts.append(f"昨夜仅睡 {sleep_hours}h，睡眠不足是当前主要限制")
            warnings.append(f"睡眠不足会直接影响训练效果和恢复速度，今晚务必早睡")
        elif sleep_hours > 0:
            recovery_parts.append(f"昨夜睡眠 {sleep_hours}h（数据可能不完整）")
        else:
            recovery_parts.append("睡眠数据未同步（不代表没睡）")

        # HRV + RHR 联合分析 + 身体电量（恢复指标被省略时不解释）
        if not recovery_omitted:
            if hrv_status in ("BALANCED", "balanced") and rhr <= 45:
                recovery_parts.append(f"HRV 平衡、静息心率 {rhr} bpm 处于优秀区间，自主神经系统状态良好")
            elif hrv_status in ("UNBALANCED", "unbalanced", "LOW", "low"):
                recovery_parts.append(f"HRV 偏低、静息心率 {rhr} bpm，提示身体可能处于应激状态")
                warnings.append("HRV 异常时优先保证睡眠和营养，降低训练强度")

            # 身体电量
            if bb >= 90:
                recovery_parts.append(f"晨起身体电量 {bb}%，能量储备充足")
            elif bb >= 70:
                recovery_parts.append(f"晨起身体电量 {bb}%，处于正常范围")
            elif bb > 0:
                recovery_parts.append(f"晨起身体电量仅 {bb}%，能量储备偏低")
                if sleep_hours < 7 and not sleep_omitted:
                    recommendations.append("身体电量偏低与睡眠不足高度相关，改善睡眠是第一优先级")

        # ── 训练负荷分析 ──
        acwr = load.get("acwr", 1.0)
        acwr_status = load.get("acwr_status", "optimal")
        acute = load.get("acute_load_7d", 0)
        chronic = load.get("chronic_load_28d", 0)

        # ACWR 及 7 天/28 天负荷对比在"近 7 天负荷与恢复"观察块输出，这里只保留风险警告。
        # 负荷维度被省略时（如 28 天覆盖不足）不输出 ACWR 相关结论。
        if not load_omitted:
            if acwr_status == "overreaching":
                warnings.append("短期负荷上升较快，建议本周安排 1-2 天轻松训练或主动恢复")
            elif acwr_status == "high_risk":
                warnings.append("近 7 天负荷相对 28 天基准过高，建议安排减量训练")

        # ── 当日训练分析 ──
        is_rest = activity.get("is_rest_day", False)
        sessions = activity.get("sessions", [])
        total_dur = activity.get("total_duration_min", 0)
        total_dist = activity.get("total_distance_km", 0) or 0
        total_load = activity.get("total_training_load", 0)
        activity_level = activity.get("activity_level", "sedentary")

        activity_state = activity.get("activity_state")
        if activity_state == ActivityDayState.UNKNOWN:
            observations.append("运动数据尚未同步，无法判断当天是否训练或休息")
        elif is_rest:
            if activity_level == "very_active":
                observations.append("当日虽无正式训练，但全天活动量很高（步数 >20000），相当于一次中等强度有氧")
            elif activity_level == "active":
                observations.append("当日为休息日，保持了适度活动，有利于主动恢复")
            else:
                observations.append("当日为完全休息日，身体得到了恢复")
        else:
            # ── 当天跑步训练分析（只关注跑步，合并多 session 为一次洞察）──
            runs = []
            running_session_ids = {
                str(session.get("training_analysis", {}).get("activity_id") or "")
                for session in sessions
                if session.get("type") == "running"
                and isinstance(session.get("training_analysis"), dict)
            }
            for analysis in session_analyses or []:
                # 是否为跑步来自原始活动模态；primary_type 只表示有氧/节奏/
                # 间歇等课型。课型 unknown 不能把真实跑步降成“非跑步”。
                is_running = analysis.get("is_running")
                if is_running is None:
                    activity_id = str(analysis.get("activity_id") or "")
                    is_running = activity_id in running_session_ids or str(
                        analysis.get("primary_type") or ""
                    ) in {"running", "aerobic", "tempo", "interval", "fartlek", "long"}
                if is_running:
                    runs.append(analysis)
            if runs:
                # 1) 运动概要：汇总当日所有跑步
                total_run_km = 0.0
                total_run_s = 0
                run_labels: list[str] = []
                for analysis in runs:
                    session_summary = analysis.get("session_summary") or {}
                    volume = session_summary.get("volume") or {}
                    classification = analysis.get("structure_classification") or {}
                    pace_profile = session_summary.get("pace_profile") or {}
                    d_km = (volume.get("distance_m") or 0) / 1000
                    d_s = volume.get("duration_sec") or volume.get("duration_s") or 0
                    total_run_km += d_km
                    total_run_s += d_s
                    # 课型标签
                    structure_type = classification.get("structure_type")
                    if structure_type and structure_type != "unknown":
                        label = str(classification.get("label") or structure_type)
                        alternations = int(classification.get("alternations") or 0)
                        if alternations:
                            label += f"（{alternations} 组快慢交替）"
                        run_labels.append(label)
                    elif d_km > 0:
                        pace = pace_profile.get("avg_pace_sec_per_km") or (volume.get("pace_sec_per_km") or 0)
                        if pace:
                            run_labels.append(f"{d_km:.1f}km {MemoryWriter._format_pace(pace)}/km")
                        else:
                            run_labels.append(f"{d_km:.1f}km")
                overview_summary = f"当日跑步 {total_run_km:.1f} km"
                if total_run_s:
                    overview_summary += f"，累计用时 {int(total_run_s // 60)} min"
                if run_labels:
                    overview_summary += "（" + "、".join(run_labels) + "）"
                unknown_type_count = sum(
                    str(item.get("primary_type") or "unknown") == "unknown"
                    for item in runs
                )
                if unknown_type_count:
                    overview_summary += (
                        f"；其中 {unknown_type_count} 次因时长或强度证据不足，"
                        "具体课型暂无法可靠判定"
                    )
                overview_summary += "。"
                observations.append("运动概要：" + overview_summary)

                # 2) 逐 session 的强度/动力学/分析（只输出跑步 session）
                for idx, analysis in enumerate(runs):
                    session_summary = analysis.get("session_summary") or {}
                    volume = session_summary.get("volume") or {}
                    classification = analysis.get("structure_classification") or {}
                    intensity = session_summary.get("intensity") or {}
                    structure = session_summary.get("structure") or {}
                    pace_profile = session_summary.get("pace_profile") or {}
                    pace = pace_profile.get("avg_pace_sec_per_km") or (volume.get("pace_sec_per_km") or 0) or 0
                    d_km = (volume.get("distance_m") or 0) / 1000
                    d_s = volume.get("duration_sec") or volume.get("duration_s") or 0
                    prefix = f"第{idx + 1}次跑步 " if len(runs) > 1 else ""

                    # 强度分布
                    intensity_parts: list[str] = []
                    pace_bands = intensity.get("pace_bands_pct") or {}
                    hr_bands = intensity.get("hr_bands_pct") or {}
                    if pace_bands:
                        top_pace_key = max(pace_bands, key=pace_bands.get)
                        if top_pace_key.startswith("-inf"):
                            top_pace_text = f"<{MemoryWriter._format_pace(float(top_pace_key[len('-inf-'):]))}"
                        elif top_pace_key.endswith("inf"):
                            top_pace_text = f">{MemoryWriter._format_pace(float(top_pace_key[:-4]))}"
                        else:
                            low, high = top_pace_key.split("-", 1)
                            top_pace_text = (
                                f"{MemoryWriter._format_pace(float(low))}–"
                                f"{MemoryWriter._format_pace(float(high))}"
                            )
                        if top_pace_text:
                            text = f"配速以 {top_pace_text}/km 为主（{pace_bands[top_pace_key]:.0f}%）"
                            if intensity.get("basis") == "pace":
                                text += "（依据配速，心率缺失）"
                            intensity_parts.append(text)
                    elif pace_profile.get("p50"):
                        p50_text = MemoryWriter._format_pace(pace_profile.get("p50"))
                        if p50_text:
                            intensity_parts.append(f"配速中位数约 {p50_text}/km")
                    if hr_bands:
                        top_hr_key = max(hr_bands, key=hr_bands.get)
                        top_hr_text = MemoryWriter._band_range_text(top_hr_key)
                        if top_hr_text:
                            intensity_parts.append(f"心率以 {top_hr_text} bpm 为主（{hr_bands[top_hr_key]:.0f}%）")
                    cadence = structure.get("avg_cadence")
                    stride = structure.get("avg_stride")
                    if cadence:
                        cadence_text = f"平均步频 {cadence:.0f} spm"
                        if stride:
                            cadence_text += f"、步幅 {stride / 100:.2f} m"
                        intensity_parts.append(cadence_text)
                    elif stride:
                        intensity_parts.append(f"平均步幅 {stride / 100:.2f} m")
                    effect_text = MemoryWriter._effect_explanation(
                        session_summary.get("effect") or {},
                    )
                    if effect_text:
                        intensity_parts.append(f"训练效果 {effect_text}")
                    if intensity_parts:
                        observations.append(prefix + "强度分布：" + ";".join(intensity_parts) + "。")

                    # 跑步动力学
                    kinematics_parts: list[str] = []
                    cadence_cv = structure.get("cadence_cv_pct")
                    cadence_half = structure.get("cadence_half_diff")
                    stride_cv = structure.get("stride_cv_pct")
                    stride_half = structure.get("stride_half_diff")
                    gct_cv = structure.get("gct_cv_pct")
                    gct_half = structure.get("gct_half_diff")
                    vo_cv = structure.get("vo_cv_pct")
                    avg_vr = structure.get("avg_vertical_ratio")
                    hr_cv = structure.get("hr_cv_pct")
                    hr_half = structure.get("hr_half_diff")
                    if avg_vr is not None:
                        kinematics_parts.append(f"垂直振幅比 {avg_vr:.1f}%")
                    if cadence_cv is not None:
                        kinematics_parts.append(f"步频 CV {cadence_cv:.1f}%")
                    if cadence_half is not None:
                        direction = "后半程升高" if cadence_half > 0 else "后半程降低" if cadence_half < 0 else "稳定"
                        kinematics_parts.append(f"步频前后半程差 {cadence_half:+.1f} spm（{direction}）")
                    if stride_cv is not None:
                        kinematics_parts.append(f"步幅 CV {stride_cv:.1f}%")
                    if stride_half is not None:
                        direction = "后半程缩" if stride_half < 0 else "后半程增" if stride_half > 0 else "稳定"
                        kinematics_parts.append(f"步幅前后半程差 {stride_half:+.1f} cm（{direction}）")
                    if gct_cv is not None:
                        kinematics_parts.append(f"触地时间 CV {gct_cv:.1f}%")
                    if vo_cv is not None:
                        kinematics_parts.append(f"垂直振幅 CV {vo_cv:.1f}%")
                    if hr_cv is not None:
                        kinematics_parts.append(f"心率 CV {hr_cv:.1f}%")
                    if hr_half is not None:
                        direction = "心率漂移" if hr_half > 2 else "稳定"
                        kinematics_parts.append(f"心率前后半程差 {hr_half:+.1f} bpm（{direction}）")
                    stable_indicators = 0
                    total_indicators = 0
                    for cv_val, half_val in [(cadence_cv, cadence_half), (stride_cv, stride_half), (gct_cv, gct_half)]:
                        if cv_val is not None:
                            total_indicators += 1
                            if cv_val < 5:
                                stable_indicators += 1
                        if half_val is not None:
                            total_indicators += 1
                            if abs(half_val) < (3 if cv_val is not None and cv_val < 5 else 5):
                                stable_indicators += 1
                    if total_indicators >= 3:
                        ratio = stable_indicators / total_indicators
                        if ratio >= 0.8:
                            kinematics_parts.append("技术稳定性：优秀")
                        elif ratio >= 0.5:
                            kinematics_parts.append("技术稳定性：一般")
                        else:
                            kinematics_parts.append("技术稳定性：需关注，后半程出现明显代偿")
                    if kinematics_parts:
                        observations.append(prefix + "跑步动力学：" + "；".join(kinematics_parts) + "。")

                    # 跑步分析（S4-S8）
                    ra = analysis.get("running_analysis")
                    if ra:
                        ra_parts: list[str] = []
                        drift = ra.get("aerobic_drift")
                        if drift and drift.get("status") == "ok" and drift.get("grade"):
                            grade = drift["grade"]
                            rate = drift.get("drift_rate_pct")
                            grade_labels = {"excellent": "优秀", "normal": "正常", "elevated": "偏高", "high": "高"}
                            label = grade_labels.get(grade, grade)
                            if rate is not None:
                                ra_parts.append(f"有氧漂移 {rate:.1f}%/h（{label}）")
                            else:
                                ra_parts.append(f"有氧漂移评级：{label}")
                        economy = ra.get("economy")
                        if economy and economy.get("status") == "ok":
                            gait = economy.get("gait_label")
                            trend = economy.get("trend_pct")
                            has_baseline = bool(economy.get("baseline_available"))
                            gait_verdict = {
                                "均衡高效型": "经济性良好",
                                "高步频省力型": "经济性较好",
                                "大步幅低步频型": "步频偏低、冲击风险偏高",
                                "低效型": "经济性偏低",
                            }
                            # 有历史基线时，同配速趋势比绝对指数更有意义
                            if has_baseline and trend is not None:
                                direction = "提升" if trend > 0 else "下降"
                                trend_days = int(economy.get("trend_days") or 30)
                                ra_parts.append(
                                    f"经济性较近{trend_days}天{direction} {abs(trend):.0f}%"
                                )
                            # 步态标签给出定性结论
                            if gait and gait != "unknown":
                                verdict = gait_verdict.get(gait)
                                if verdict:
                                    ra_parts.append(f"步态：{gait}（{verdict}）")
                                else:
                                    ra_parts.append(f"步态：{gait}")
                            # 既无基线也无步态结论时才回退到绝对指数
                            if not (has_baseline and trend is not None) and (not gait or gait == "unknown"):
                                ei = economy.get("economy_index")
                                if ei is not None:
                                    ra_parts.append(f"经济性指数 {ei:.1f}（同配速下越高越好）")
                        fatigue = ra.get("fatigue_compensation")
                        if fatigue and fatigue.get("status") == "ok":
                            pattern = fatigue.get("pattern_label") or fatigue.get("pattern")
                            if pattern and pattern != "no_significant_fatigue":
                                ra_parts.append(f"疲劳模式：{pattern}")
                                suggestion = fatigue.get("training_suggestion")
                                if suggestion:
                                    recommendations.append(suggestion)
                        decoupling = ra.get("decoupling")
                        if decoupling and decoupling.get("status") == "ok":
                            d_type = decoupling.get("decoupling_label") or decoupling.get("decoupling_type")
                            if d_type and d_type not in ("none", None):
                                ra_parts.append(f"解耦类型：{d_type}")
                                attribution = decoupling.get("attribution")
                                if attribution:
                                    ra_parts.append(attribution)
                        env = ra.get("environment")
                        if env and env.get("status") == "ok":
                            terrain = env.get("terrain_class")
                            if terrain and terrain != "flat":
                                terrain_labels = {"rolling": "起伏", "hilly": "多坡", "mountain": "山地", "steep_mountain": "陡山"}
                                ra_parts.append(f"地形：{terrain_labels.get(terrain, terrain)}")
                                normalized = env.get("normalized_pace_sec_per_km")
                                if normalized:
                                    ra_parts.append(f"等效平地配速 {MemoryWriter._format_pace(normalized)}/km")
                        if ra_parts:
                            observations.append(prefix + "跑步分析：" + "；".join(ra_parts) + "。")
            else:
                # 当天有运动但都不是跑步
                observations.append("当日运动为非跑步类型，未纳入跑步教练分析。")

        # 室内占比分析
        indoor = any("室内" in s.get("name", "") for s in sessions)
        outdoor = any(s.get("type") == "running" and "室内" not in s.get("name", "")
                      for s in sessions)
        if indoor and outdoor:
            observations.append("当日兼顾了室外和室内训练，室外保持路感，室内补充跑量")

        # ── 综合恢复评分解读（恢复指标被省略时不给出评分结论）──
        rec_score = recovery.get("overall_score", 0)
        rec_level = recovery.get("level", "fair")
        if recovery_omitted:
            recovery_parts.append("恢复指标缺失，未给出恢复评分结论")
        elif rec_level == "excellent":
            recovery_parts.append(f"综合恢复评分 {rec_score}/100，身体处于最佳状态")
        elif rec_level == "good":
            recovery_parts.append(f"综合恢复评分 {rec_score}/100，状态良好可正常训练")
        elif rec_level == "fair":
            recovery_parts.append(f"综合恢复评分 {rec_score}/100，状态一般")

        if recovery_parts:
            observations.append("恢复分析：" + "；".join(recovery_parts) + "。")

        # ── 近 7 天负荷与恢复分析（趋势维度，与当日恢复不重复）──
        week_parts: list[str] = []
        if not load_omitted and (acute or 0) > 0 and (chronic or 0) > 0:
            acwr_text = f"近 7 天训练负荷 {acute:.0f}，相对近 28 天基准（{chronic:.0f}/周）ACWR {acwr}"
            if acwr_status == "undertraining":
                acwr_text += "，负荷偏轻"
            elif acwr_status == "optimal":
                acwr_text += "，处于最优区间"
            elif acwr_status == "overreaching":
                acwr_text += "，偏高、接近过度训练边界"
            elif acwr_status == "high_risk":
                acwr_text += "，过高、存在过度训练风险"
            week_parts.append(acwr_text)
        elif not load_omitted and (acute or 0) > 0:
            week_parts.append(f"近 7 天训练负荷 {acute:.0f}（缺少 28 天基准，无法判断相对位置）")
        if not trends_omitted and seven_day:
            sleep_vals = [
                m.get("sleep_duration_hours") for m in seven_day
                if m.get("sleep_duration_hours")
            ]
            if len(sleep_vals) >= 5:
                recent_avg = sum(sleep_vals[-3:]) / 3
                older_avg = sum(sleep_vals[:-3]) / max(len(sleep_vals[:-3]), 1)
                if older_avg - recent_avg > 0.5:
                    week_parts.append(f"睡眠近 3 天走低（{older_avg:.1f}h → {recent_avg:.1f}h）")
                    recommendations.append("连续几天睡眠不足会累积疲劳，建议今晚设定一个早睡闹钟")
                elif recent_avg - older_avg > 0.5:
                    week_parts.append(f"睡眠近 3 天回升（{older_avg:.1f}h → {recent_avg:.1f}h）")
            hrv_vals = [
                m.get("hrv_last_night_avg") for m in seven_day
                if m.get("hrv_last_night_avg")
            ]
            if len(hrv_vals) >= 4:
                recent_hrv = sum(hrv_vals[-2:]) / 2
                older_hrv = sum(hrv_vals[:-2]) / max(len(hrv_vals[:-2]), 1)
                if recent_hrv - older_hrv > 3:
                    week_parts.append(f"HRV 呈回升趋势（{older_hrv:.0f} → {recent_hrv:.0f} ms）")
                elif older_hrv - recent_hrv > 3:
                    week_parts.append(f"HRV 近期走低（{older_hrv:.0f} → {recent_hrv:.0f} ms），疲劳在累积")
        if week_parts:
            observations.append("近 7 天负荷与恢复：" + "；".join(week_parts) + "。")

        # ── 运动员画像相关观察 ──
        if fitness_level:
            observations.append(f"运动员水平: {fitness_level}")
        if goal_context:
            observations.append(f"训练目标: {goal_context}")

        # ── 日报级确定性分析（S9/S10/S11/S13）──
        ra_daily = running_analysis_daily or {}
        if ra_daily.get("status") == "ok":
            # S9: HRV 基线
            hrv_base = ra_daily.get("hrv_baseline") or {}
            if hrv_base.get("status") == "ok":
                ri = hrv_base.get("recovery_index")
                rl = hrv_base.get("recovery_label")
                if ri is not None and rl:
                    observations.append(f"HRV恢复指数 {ri:.0f}/100（{rl}）")
                training_risk = hrv_base.get("training_risk")
                if training_risk and training_risk != "none":
                    warnings.append(f"HRV提示训练风险：{training_risk}")
            # ACWR 已在“近 7 天负荷与恢复”使用唯一日报口径输出；日报级
            # running_analysis 只把同一值交给伤病风险，不重复生成第二套结论。
            # S11: 恢复-表现一致性
            consistency = ra_daily.get("consistency") or {}
            if consistency.get("status") == "ok":
                pattern = consistency.get("pattern_label") or consistency.get("pattern")
                if pattern and pattern != "normal":
                    observations.append(f"恢复-表现一致性：{pattern}")
                    explanation = consistency.get("explanation")
                    if explanation:
                        observations.append(explanation)
                    ts = consistency.get("training_suggestion")
                    if ts:
                        recommendations.append(ts)
            # S13: 伤病风险
            injury = ra_daily.get("injury_risk") or {}
            if injury.get("status") == "ok":
                risk_score = injury.get("risk_score")
                risk_level = injury.get("risk_level")
                if risk_score is not None and risk_level:
                    observations.append(f"伤病风险 {risk_score:.0f}/100（{risk_level}）")
                    primary = injury.get("primary_risk_source")
                    if primary:
                        observations.append(f"主要风险源：{primary}")
                    action = injury.get("action")
                    if action:
                        recommendations.append(action)

        # ── 核心结论（结合画像；缺失维度不参与判断）──
        recovery_ok = not recovery_omitted and rec_level in ("excellent", "good")
        load_ok = not load_omitted and acwr_status == "optimal"
        if recovery_ok and load_ok:
            if fitness_level and "精英" in fitness_level:
                conclusion = f"作为{fitness_level}选手，当前状态良好。保持训练质量，关注技术细节和恢复节奏。"
            elif active_goals:
                conclusion = f"状态良好，训练负荷合理。{goal_context}——按计划推进，重点关注训练一致性。"
            else:
                conclusion = "整体状态良好，训练负荷合理，按计划执行即可。"
            confidence = "high"
        elif (not recovery_omitted and rec_level == "poor") or (not load_omitted and acwr_status in ("overreaching", "high_risk")):
            conclusion = "身体发出恢复不足的信号，建议今天以轻松恢复为主。今天的让步是为了明天更好的训练。"
            confidence = "high"
        elif not sleep_omitted and sleep_hours < 7:
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
            "generation_mode": "deterministic_fallback",
            "semantic_status": "unavailable",
            "fallback_reason": "在线 AI 未生成有效结果，当前展示本地规则分析",
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
    def _merge_platform_thresholds(
        profile: dict[str, Any] | None,
        db: Any,
        user_id: int,
        target_time: Any,
    ) -> dict[str, Any] | None:
        """把平台自算乳酸阈值（lthr/ltsp）并入档案，仅填充档案缺失的显式阈值。

        优先级：用户档案显式阈值 > 平台 lthr/ltsp > 档案推导（Karvonen/PB）> 无。
        平台阈值来自 Coros /analyse/query（lthr=阈值心率 bpm，ltsp=阈值配速 s/km）。
        """
        try:
            from datetime import date as _date
            target_date = _date.fromisoformat(str(target_time)[:10])
        except (TypeError, ValueError):
            return profile
        thresholds = load_platform_thresholds(db, user_id, target_date)
        if not thresholds:
            return profile
        merged = dict(profile or {})
        for key in ("threshold_heart_rate", "threshold_pace_sec_per_km"):
            if not merged.get(key) and thresholds.get(key):
                merged[key] = thresholds[key]
        return merged

    @staticmethod
    def _load_active_goals(memory_store) -> list[dict[str, Any]]:
        """从 memory store 加载活跃目标。"""
        try:
            goals = memory_store.list_by_type("goal", status="active")
            return [g.front_matter for g in goals]
        except Exception:
            return []


    def _load_plan_context(self, target_date: date) -> dict[str, Any]:
        """按报告日期加载当时生效的训练方案快照。"""
        try:
            # 延迟导入避免 training.py 与 memory.py 的模块级循环依赖。
            from .training import TrainingService

            return TrainingService(self._root).resolve_plan_context(target_date)
        except Exception:
            logger.exception("按日期加载训练方案上下文失败")
            return {
                "status": "plan_context_unavailable",
                "exists": False,
                "target_date": str(target_date),
                "comparison_status": "not_applicable",
            }

    @staticmethod
    def _build_plan_execution_summary(
        plan_context: dict[str, Any], activity_summary: dict[str, Any],
        week_activities: list[dict[str, Any]], target_date: date,
        recommendation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成日报可读的计划执行快照，不将未知活动覆盖当成缺席。"""
        goal = plan_context.get("goal") or {}
        target_date_value = str(goal.get("target_date") or "")
        try:
            days_to_goal = (date.fromisoformat(target_date_value) - target_date).days
        except ValueError:
            days_to_goal = None
        def is_running(activity: dict[str, Any]) -> bool:
            activity_type = str(
                activity.get("activity_type") or activity.get("activity_type_name") or ""
            ).lower()
            activity_name = str(activity.get("activity_name") or "").lower()
            return "run" in activity_type or "跑" in activity_name or "run" in activity_name

        weekly_km = round(sum(
            float(item.get("distance_meters") or item.get("distance") or 0) / 1000
            for item in week_activities if is_running(item)
        ), 1)
        recommendation = recommendation or {}
        recovery_advice = str(recommendation.get("training_advice") or "")
        cautions = [str(item) for item in recommendation.get("caution") or [] if item]
        guidance = recovery_advice if recommendation.get("status") != "unavailable" else ""
        base = {
            "comparison_status": str(plan_context.get("comparison_status") or "not_applicable"),
            "goal": {
                "name": str(goal.get("name") or "当前训练目标"),
                "target_date": target_date_value or None,
                "days_to_goal": days_to_goal,
                "status": "in_progress" if days_to_goal is None or days_to_goal >= 0 else "past_due",
            },
            "phase": copy.deepcopy(plan_context.get("current_phase") or {}),
            "week": {
                "week_start": plan_context.get("week_start"),
                "week_end": plan_context.get("week_end"),
                "target_km": plan_context.get("weekly_mileage_target"),
                "recorded_km": weekly_km,
                "label": "截至报告日已记录跑量",
            },
            "training_url": f"/training?date={target_date.isoformat()}",
            "guidance": guidance or None,
            "caution": cautions[:2],
        }
        status = str(plan_context.get("status") or "no_effective_plan")
        if base["comparison_status"] != "applicable":
            reason = {
                "draft_available": "该日只有方案草稿，尚不评价计划执行。",
                "scheduled_plan": "方案将在未来生效，该日不评价计划执行。",
                "no_effective_plan": "该日没有生效训练方案，无法比较计划执行。",
            }.get(status, "该日计划上下文不可用，暂不评价执行。")
            return {**base, "status": "not_applicable", "headline": reason, "actual": None}

        workout = ensure_training_prescription(plan_context.get("workout") or {})
        planned = {
            "title": str(workout.get("title") or "当日训练"),
            "purpose": str(workout.get("purpose") or "按计划完成训练"),
            "intensity": str(workout.get("intensity") or ""),
            "distance_km": workout.get("distance_km"),
            "duration_minutes": workout.get("duration_minutes"),
            "training_prescription": workout.get("training_prescription"),
        }
        state = str(activity_summary.get("activity_state") or "unknown")
        if state == ActivityDayState.UNKNOWN.value:
            return {
                **base, "status": "awaiting_sync", "planned": planned, "actual": None,
                "headline": "运动数据尚未同步，暂不判断今天是否按计划执行。",
                "sync_url": f"/sync?date={target_date.isoformat()}&from=reports&return_to=/reports?date={target_date.isoformat()}#single-sync",
            }

        sessions = activity_summary.get("sessions") or []
        has_running = any(str(item.get("type") or "") == "running" for item in sessions)
        actual = {
            "summary": (
                "已确认休息" if activity_summary.get("is_rest_day")
                else ("已记录跑步训练" if has_running else "已记录其他运动")
            ),
            "distance_km": activity_summary.get("total_distance_km"),
            "duration_minutes": activity_summary.get("total_duration_min"),
        }
        prescription = planned.get("training_prescription") or {}
        primary = str(prescription.get("primary_completion") or "distance")
        target_value = planned.get("distance_km") if primary == "distance" else planned.get("duration_minutes")
        actual_value = actual.get("distance_km") if primary == "distance" else actual.get("duration_minutes")
        try:
            completion_ratio = float(actual_value or 0) / float(target_value or 0)
        except (TypeError, ValueError, ZeroDivisionError):
            completion_ratio = None
        comparison = {
            "primary_metric": "距离" if primary == "distance" else "时长",
            "target": target_value,
            "actual": actual_value,
            "ratio": round(completion_ratio, 2) if completion_ratio is not None else None,
            "planned_structure": workout_steps_summary(prescription) or None,
            "structure": (
                "计划已包含逐段结构；当前同步摘要未提供足够的逐段完成证据"
                if prescription.get("structure_version") == 2
                else "当前同步摘要未提供足够训练块证据"
            ),
            "intensity": "当前同步摘要未提供足够个人强度证据",
        }
        if workout.get("type") == "rest":
            headline = "今天按计划以恢复为主。" if activity_summary.get("is_rest_day") else "今天记录了活动；恢复安排是否需要调整，请在训练页查看。"
            execution_status = "aligned" if activity_summary.get("is_rest_day") else "review"
        elif has_running:
            if completion_ratio is not None and completion_ratio >= 0.85:
                headline = "今日主要距离或时长已达到处方目标；训练块与强度证据仍以活动详情为准。"
                execution_status = "completed"
            else:
                headline = "今天已完成部分跑步训练；请结合训练块和体感决定是否需要调整后续安排。"
                execution_status = "partially_completed"
        elif activity_summary.get("is_rest_day"):
            headline = "今天未记录与计划对应的训练；如有现实约束，可在训练页说明并查看调整建议。"
            execution_status = "skipped"
        else:
            headline = "今天记录了其他运动；是否作为替代训练，请在训练页确认。"
            execution_status = "unmatched"
        return {
            **base, "status": execution_status, "planned": planned,
            "actual": actual, "comparison": comparison, "headline": headline,
        }

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
    def _band_range_text(band_key: str) -> str:
        """把心率/配速带 key（-inf-130 / 130-145 / 175-inf）转成可读文本。"""
        if band_key.startswith("-inf"):
            return f"<{float(band_key[len('-inf-'):]):g}"
        if band_key.endswith("inf"):
            return f">{float(band_key[:-4]):g}"
        low, high = band_key.split("-", 1)
        return f"{float(low):g}–{float(high):g}"

    @staticmethod
    def _format_pace(pace_sec_per_km: Any) -> str:
        """把秒/km 配速格式化为 m'ss"/km 文本；无效值返回空串。"""
        try:
            value = float(pace_sec_per_km)
        except (TypeError, ValueError):
            return ""
        if not 0 < value < 3600:
            return ""
        minutes = int(value // 60)
        seconds = int(round(value % 60))
        if seconds == 60:
            minutes += 1
            seconds = 0
        return f"{minutes}'{seconds:02d}\""

    @staticmethod
    def _format_duration(seconds: Any) -> str:
        try:
            secs = float(seconds)
        except (TypeError, ValueError):
            return "—"
        if secs <= 0:
            return "—"
        hours = int(secs // 3600)
        minutes = int((secs % 3600) // 60)
        return f"{hours}h{minutes:02d}min" if hours else f"{minutes}min"

    @staticmethod
    def _format_marathon(pace_sec_per_km: Any) -> str:
        try:
            total_sec = float(pace_sec_per_km) * 42.195
        except (TypeError, ValueError):
            return ""
        if not 0 < total_sec < 36000:
            return ""
        hours = int(total_sec // 3600)
        minutes = int((total_sec % 3600) // 60)
        seconds = int(round(total_sec % 60))
        if seconds == 60:
            minutes += 1
            seconds = 0
        if minutes == 60:
            hours += 1
            minutes = 0
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _effect_explanation(effect: dict[str, Any]) -> str:
        """训练效果简短解释（强度分布用）：TE 值 + 强度含义；估算必带依据。"""
        if not effect:
            return ""
        te = effect.get("aerobic_training_effect")
        ate = effect.get("anaerobic_training_effect")
        if te is None and ate is None:
            return ""
        parts = []
        if te is not None:
            parts.append(f"有氧 {te:g}")
        if ate is not None:
            parts.append(f"无氧 {ate:g}")
        main = ate if (ate or 0) >= (te or 0) else te
        if main is not None:
            if main >= 4.0:
                explanation = "高强度刺激，对体能提升作用明显"
            elif main >= 3.0:
                explanation = "训练成效显著，体能获得有效刺激"
            elif main >= 2.0:
                explanation = "有氧基础得到维持与改善"
            elif main >= 1.0:
                explanation = "以基础保持和恢复为主"
            else:
                explanation = ""
            if explanation:
                parts.append(explanation)
        text = "，".join(parts)
        if bool(effect.get("estimated")):
            basis = "心率" if effect.get("estimate_basis") == "heart_rate" else "配速"
            text += f"（估算，依据{basis}）"
        return text

    @staticmethod
    def _format_effect(effect: dict[str, Any]) -> str:
        """训练效果文本；估算值带 ≈ 与依据标记。"""
        if not effect:
            return "不可得"
        te = effect.get("aerobic_training_effect")
        ate = effect.get("anaerobic_training_effect")
        parts = []
        if te is not None:
            parts.append(f"有氧 {te:g}")
        if ate is not None:
            parts.append(f"无氧 {ate:g}")
        label = effect.get("label")
        if label:
            parts.append(str(label))
        mod = effect.get("moderate_intensity_minutes")
        vig = effect.get("vigorous_intensity_minutes")
        minutes = []
        if mod:
            minutes.append(f"中等 {float(mod):g}min")
        if vig:
            minutes.append(f"高 {float(vig):g}min")
        if minutes:
            parts.append("强度分钟 " + "/".join(minutes))
        if not parts:
            return "不可得"
        text = " · ".join(parts)
        if bool(effect.get("estimated")):
            basis = "心率" if effect.get("estimate_basis") == "heart_rate" else "配速"
            text = f"≈ {text}（估算，依据{basis}）"
        return text

    @staticmethod
    def _deep_analysis_lines(analysis: dict[str, Any]) -> list[str]:
        """把 session-summary-v2 的确定性事实渲染为训练深度分析 Markdown 行。"""
        summary = analysis.get("session_summary") or {}
        volume = summary.get("volume") or {}
        effect = summary.get("effect") or {}
        elev = summary.get("elevation_profile") or {}
        terrain = summary.get("terrain") or {}
        pace_profile = summary.get("pace_profile") or {}
        structure = summary.get("structure") or {}
        structure_profile = summary.get("structure_profile") or {}
        lines: list[str] = []

        distance = (volume.get("distance_m") or 0) / 1000
        duration = volume.get("duration_s") or 0
        elapsed = volume.get("elapsed_duration_s")
        duration_text = MemoryWriter._format_duration(duration)
        if elapsed and elapsed > duration + 5:
            duration_text += f"（总 {MemoryWriter._format_duration(elapsed)}，含暂停）"
        elif elapsed and abs(elapsed - duration) <= 5:
            duration_text += "（无暂停）"
        rows = [f"距离/用时: {distance:.2f} km / {duration_text}"]
        pace = pace_profile.get("avg_pace_sec_per_km")
        if pace:
            marathon = MemoryWriter._format_marathon(pace)
            suffix = f"（折全马约 {marathon}）" if marathon else ""
            rows.append(f"平均配速: {MemoryWriter._format_pace(pace)}/km{suffix}")
        fastest = pace_profile.get("fastest_pace_sec_per_km")
        if fastest:
            rows.append(f"最快配速: {MemoryWriter._format_pace(fastest)}/km")
        ascent = elev.get("ascent_m")
        descent = elev.get("descent_m")
        if ascent is not None or descent is not None:
            range_text = ""
            if elev.get("max_elevation_m") is not None and elev.get("min_elevation_m") is not None:
                range_text = f"（海拔 {elev['min_elevation_m']:g}~{elev['max_elevation_m']:g}m）"
            rows.append(f"爬升/下降: {ascent or 0:g}m / {descent or 0:g}m{range_text}")
        elif terrain and terrain.get("ascent_m") is not None:
            rows.append(f"累计爬升: {terrain['ascent_m']:g}m")
        avg_cadence = structure.get("avg_cadence")
        max_cadence = structure.get("max_cadence")
        if avg_cadence:
            cadence_text = f"{avg_cadence:.0f} spm"
            if max_cadence:
                cadence_text += f"，最大 {max_cadence:.0f}"
            rows.append(f"平均步频: {cadence_text}")
        calories = volume.get("calories")
        effect_text = MemoryWriter._format_effect(effect)
        if calories is not None:
            rows.append(f"热量/训练效果: {calories:.0f} kcal / {effect_text}")
        elif effect_text != "不可得":
            rows.append(f"训练效果: {effect_text}")
        lines.append("**整体水平**")
        lines.extend(f"- {row}" for row in rows)

        granularity = summary.get("granularity")
        if granularity in ("L1", "L2"):
            intensity = summary.get("intensity") or {}
            split_count = pace_profile.get("split_count") or 0
            pace_bands = intensity.get("pace_bands_pct") or {}
            hr_bands = intensity.get("hr_bands_pct") or {}

            def _band_text(band_key: str) -> str:
                if band_key.startswith("-inf"):
                    return f"<{MemoryWriter._format_pace(float(band_key[len('-inf-'):]))}"
                if band_key.endswith("inf"):
                    return f">{MemoryWriter._format_pace(float(band_key[:-4]))}"
                low, high = band_key.split("-", 1)
                return (
                    f"{MemoryWriter._format_pace(float(low))}–"
                    f"{MemoryWriter._format_pace(float(high))}"
                )

            lines.append("")
            lines.append("**强度分布**")
            if pace_bands:
                basis = intensity.get("basis") or ""
                bands = "；".join(
                    f"{_band_text(k)}: {v:g}%" for k, v in pace_bands.items()
                )
                lines.append(f"- 配速带分布（{split_count} 段样本，basis={basis}）: {bands}")
            percentiles = []
            for label, key in (
                ("P5", "p5"), ("P25", "p25"), ("P50", "p50"),
                ("P75", "p75"), ("P95", "p95"),
            ):
                value = pace_profile.get(key)
                if value:
                    percentiles.append(f"{label} {MemoryWriter._format_pace(value)}")
            if percentiles:
                lines.append("- 配速分位数: " + " / ".join(percentiles))
            if hr_bands:
                def _hr_band_text(band_key: str) -> str:
                    if band_key.startswith("-inf"):
                        return f"<{float(band_key[len('-inf-'):]):g}"
                    if band_key.endswith("inf"):
                        return f">{float(band_key[:-4]):g}"
                    low, high = band_key.split("-", 1)
                    return f"{float(low):g}–{float(high):g}"

                bands = "；".join(
                    f"{_hr_band_text(k)}: {v:g}%" for k, v in hr_bands.items()
                )
                lines.append(f"- 心率带分布: {bands}")

            lines.append("")
            lines.append("**配速节奏**")
            half = pace_profile.get("half_pace_diff_s")
            if half is not None:
                direction = "后程偏慢（正分段）" if half > 0 else "前程偏慢（负分段）"
                lines.append(
                    f"- 前后半程: 后半较前半 {MemoryWriter._format_pace(abs(half))}/km · {direction}"
                )
            cv = pace_profile.get("cv_pct")
            if cv is not None:
                stability = "控制力强" if cv < 8 else "存在起伏" if cv < 15 else "波动明显"
                lines.append(f"- 段间配速 CV {cv:g}%（{stability}）")
            classification = analysis.get("structure_classification") or {}
            if classification:
                label = classification.get("label") or "未知"
                conf = classification.get("confidence")
                conf_parts = []
                if conf is not None:
                    conf_parts.append(f"置信 {float(conf):.0%}")
                missing = classification.get("missing_evidence") or []
                if missing:
                    conf_parts.append("缺 " + "/".join(missing))
                alternations = classification.get("alternations") or 0
                structure_text = f"{label}"
                if alternations:
                    structure_text += f"（{alternations} 组快慢交替）"
                if conf_parts:
                    structure_text += f" · {'；'.join(conf_parts)}"
                lines.append(f"- 训练结构: {structure_text}")
                groups = classification.get("work_recovery_groups") or []
                if groups:
                    group_texts = []
                    for group in groups:
                        work_pace = group.get("work_pace_sec_per_km")
                        recovery_pace = group.get("recovery_pace_sec_per_km")
                        work_hr = group.get("work_avg_hr")
                        recovery_hr = group.get("recovery_avg_hr")
                        parts = []
                        if work_pace:
                            text = f"快 {MemoryWriter._format_pace(work_pace)}/km"
                            work_distance = group.get("work_distance_m")
                            if work_distance and classification.get("quantity_reliable") is not False:
                                text += f" · {work_distance / 1000:.1f}km"
                            if work_hr:
                                text += f"(hr{work_hr:.0f})"
                            parts.append(text)
                        if recovery_pace:
                            text = f"慢 {MemoryWriter._format_pace(recovery_pace)}/km"
                            recovery_distance = group.get("recovery_distance_m")
                            if recovery_distance and classification.get("quantity_reliable") is not False:
                                text += f" · {recovery_distance / 1000:.1f}km"
                            if recovery_hr:
                                text += f"(hr{recovery_hr:.0f})"
                            parts.append(text)
                        if parts:
                            group_texts.append(" → ".join(parts))
                    if group_texts:
                        lines.append(
                            "- 每组配速: " + "；".join(
                                f"第{i + 1}组 {text}"
                                for i, text in enumerate(group_texts)
                            )
                        )
                if classification.get("quantity_reliable") is False:
                    lines.append("- 注: 分段距离/时长与总量偏差，仅强度模式有效")
                fatigue = classification.get("fatigue_signal") or {}
                if fatigue.get("detected"):
                    lines.append(f"- 疲劳信号: {fatigue.get('note')}")
                cadence_consistency = classification.get("cadence_consistency")
                if cadence_consistency:
                    lines.append(
                        f"- 步频一致性: CV {cadence_consistency['cv_pct']:g}%"
                        f"（平均 {cadence_consistency['avg']:.0f} spm）"
                    )
            else:
                composite = structure_profile.get("composite_type")
                work = structure_profile.get("work_blocks")
                recovery = structure_profile.get("recovery_blocks")
                if composite:
                    label_map = {
                        "interval": "间歇结构", "fartlek": "变速结构",
                        "structured": "结构化分段", "steady": "平稳节奏",
                    }
                    label = label_map.get(composite, composite)
                    detail = f"（work {work}/recovery {recovery}）" if work or recovery else ""
                    lines.append(f"- 训练结构: {label}{detail}")
        else:
            lines.append("")
            lines.append("- 无分段数据，强度分布与配速节奏不可得。")
        return lines

    @staticmethod
    def _render_daily_body(fm: dict[str, Any], target_date: date) -> str:
        """渲染日报正文。"""
        ya = get_daily_activities(fm)
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
        ]
        if fm.get("data_as_of"):
            lines.append(f"> 数据截止: {str(fm['data_as_of'])[:19]}")
        if fm.get("report_finality") == "provisional":
            lines.append("> 当前为暂态报告，后续同步到新数据后应重新生成。")
        if fm.get("data_readiness") == "limited":
            omitted = "、".join(fm.get("omitted_sections", [])) or "部分分析"
            lines.append(f"> ⚠️ 数据不完整，以下内容已省略: {omitted}")
        lines.extend(["", "## 🏃 当日训练", ""])

        # 活动详情分析
        session_analyses = fm.get("session_analyses", [])
        if session_analyses:
            lines.extend(["", "## 🔬 训练细节分析", ""])
            for analysis in session_analyses:
                if isinstance(analysis, str):
                    lines.append(analysis)
                    continue
                lines.append(
                    f"### {analysis.get('activity_name') or '训练'} · "
                    f"{analysis.get('display_name') or '训练内容待识别'}"
                )
                lines.append(
                    f"- 置信度: {float(analysis.get('confidence', 0)):.0%} | "
                    f"算法: {analysis.get('algorithm_version', '—')}"
                )
                lines.extend(MemoryWriter._deep_analysis_lines(analysis))
                for implication in analysis.get("training_implications", []):
                    lines.append(f"- 后续影响: {implication}")
                lines.append("")

        if ya.get("activity_state") == ActivityDayState.UNKNOWN:
            lines.append("**运动数据未同步** — 暂时无法判断当天是否训练或休息。")
        elif ya.get("is_rest_day"):
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

        plan_execution = fm.get("plan_execution_summary") or {}
        if plan_execution:
            lines.extend(["", "## 🎯 计划执行与目标进展", ""])
            goal = plan_execution.get("goal") or {}
            phase = plan_execution.get("phase") or {}
            planned = plan_execution.get("planned") or {}
            actual = plan_execution.get("actual") or {}
            if goal.get("name"):
                goal_line = f"- 目标：{goal['name']}"
                if goal.get("target_date"):
                    goal_line += f" · 目标日期 {goal['target_date']}"
                lines.append(goal_line)
            if phase.get("name"):
                lines.append(f"- 当前阶段：{phase['name']}")
            if planned:
                lines.append(f"- 当日计划：{planned.get('title', '—')}")
            if actual:
                lines.append(f"- 已记录：{actual.get('summary', '—')}")
            week = plan_execution.get("week") or {}
            if week.get("target_km") is not None:
                lines.append(
                    f"- 本周已记录：{week.get('recorded_km', 0)} / {week['target_km']} km"
                )
            if plan_execution.get("headline"):
                lines.append(f"- 下一步：{plan_execution['headline']}")
            if plan_execution.get("guidance"):
                lines.append(f"- 调整建议：{plan_execution['guidance']}")
            for caution in plan_execution.get("caution") or []:
                lines.append(f"- 注意：{caution}")

        lines.extend(["", "## 😴 睡眠恢复", ""])
        if sleep.get("status") == "unavailable":
            lines.append(f"- 数据不可用: {sleep.get('reason', '睡眠数据不完整')}")
        else:
            lines.append(
                f"- **睡眠时长**: {sleep.get('total_hours', '?')}h | "
                f"评分 {sleep.get('sleep_score', '?')} ({sleep.get('quality', '?')})"
            )

        lines.extend(["", "## 📊 今晨状态", ""])
        if morning.get("status") == "unavailable":
            lines.append(f"- 数据不可用: {morning.get('reason', '恢复指标不完整')}")
        else:
            lines.extend([
                f"| 指标 | 数值 |",
                f"|------|------|",
                f"| 静息心率 | {morning.get('resting_hr', '?')} bpm |",
                f"| HRV | {morning.get('hrv_ms', '?')} ms |",
                f"| 身体电量 | {morning.get('body_battery_morning', '?')} |",
                f"| 训练准备 | {morning.get('training_readiness_score', '?')} |",
            ])

        lines.extend(["", "## 📈 负荷状态", ""])
        if load.get("status") == "unavailable":
            lines.append(f"- 数据不可用: {load.get('reason', '活动历史覆盖不足')}")
        else:
            lines.append(
                f"- **ACWR**: {load.get('acwr', '?')} — {load.get('acwr_status', '?')}"
            )
        if recovery.get("status") == "unavailable":
            lines.append(f"- 恢复评分不可用: {recovery.get('reason', '恢复数据不足')}")
        else:
            lines.append(
                f"- **恢复评分**: {recovery.get('overall_score', '?')}/100 "
                f"({recovery.get('level', '?')})"
            )

        athlete = fm.get("athlete_context") or {}
        if athlete.get("status") == "available":
            cap = (athlete.get("capacity_profile") or {}).get(
                "current_sustainable_capacity"
            ) or {}
            background_parts = []
            weekly = cap.get("weekly_km")
            if weekly:
                background_parts.append(
                    f"可持续周跑量参考约 {float(weekly):g} km"
                )
            long_run = cap.get("long_run_km")
            if long_run:
                background_parts.append(f"长距离参考 {float(long_run):g} km")
            pace = cap.get("recent_running_pace_sec_per_km")
            if pace:
                background_parts.append(
                    f"参考配速 {MemoryWriter._format_pace(pace)}/km"
                )
            if background_parts:
                cutoff = athlete.get("facts_cutoff") or ""
                suffix = f"（数据截至 {cutoff}）" if cutoff else ""
                lines.append("")
                lines.append(
                    f"- **能力背景**: {'；'.join(background_parts)}{suffix}"
                )

        lines.extend([
            "", "## 🎯 当日训练建议", "",
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
            f"*本报告由 neurun daily 自动生成*",
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
    def generate_daily_report(
        self,
        user_id: str,
        target_date: date | None = None,
        ai_insight: dict[str, Any] | None = None,
        *,
        readiness: dict[str, Any],
        athlete_context: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> Memory:
        return self.writer.generate_daily_report(
            user_id, target_date, ai_insight, readiness=readiness,
            athlete_context=athlete_context, persist=persist,
        )

    def finalize_daily_report(
        self,
        memory: Memory,
        ai_insight: dict[str, Any] | None = None,
    ) -> Memory:
        return self.writer.finalize_daily_report(memory, ai_insight)

    def generate_weekly_summary(self, user_id: str,
                                 target_date: date | None = None) -> Memory:
        return self.writer.generate_weekly_summary(user_id, target_date)

    def generate_recovery_summary(self, user_id: str,
                                   target_date: date | None = None) -> Memory:
        return self.writer.generate_recovery_summary(user_id, target_date)

    def get_training_history_entries(
        self,
        user_id: int | None = None,
        days: int = 30,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """直接从 SQLite 构建完整训练史，不依赖历史日报文件。"""
        days = max(1, min(int(days), 90))
        end_date = end_date or date.today()
        start_date = end_date - timedelta(days=days - 1)
        db = self.writer._db()
        if user_id is None:
            try:
                from sqlalchemy import text

                session = db.get_session()
                row = session.execute(text("""
                    SELECT user_id FROM activities
                    UNION ALL
                    SELECT user_id FROM daily_health_metrics
                    LIMIT 1
                """)).fetchone()
                session.close()
                user_id = int(row[0]) if row else None
            except Exception:
                user_id = None
        if user_id is None:
            return []
        activities = self.writer._safe_get_activities(
            db, user_id, start_date, end_date,
        )
        try:
            health_rows = db.get_health_metrics(user_id, start_date, end_date) or []
        except Exception:
            health_rows = []

        # 历史分析直接基于原始活动生成，不要求对应日期已有日报。
        self.writer._analyze_training_sessions(
            db, user_id, activities, activities, {},
        )

        activities_by_date: dict[str, list[dict[str, Any]]] = {}
        for activity in activities:
            key = str(
                activity.get("activity_date")
                or activity.get("start_time")
                or ""
            )[:10]
            if key:
                activities_by_date.setdefault(key, []).append(activity)

        health_by_date: dict[str, dict[str, Any]] = {}
        for health in health_rows:
            key = str(
                health.get("metric_date")
                or health.get("date")
                or ""
            )[:10]
            if key:
                health_by_date[key] = health

        entries = []
        for offset in range(days):
            current = end_date - timedelta(days=offset)
            key = str(current)
            daily_activities = activities_by_date.get(key, [])
            health = health_by_date.get(key, {})
            activity_state = self.writer._get_activity_day_state(
                db, user_id, current, daily_activities,
            )
            activity_summary = self.writer._summarize_activities(
                daily_activities, health, activity_state=activity_state,
            )
            sleep = self.writer._summarize_sleep(health)
            morning = self.writer._summarize_morning(health)
            recovery = self.writer._calc_recovery_score(health, health) if health else {}
            training_types = []
            for session in activity_summary.get("sessions", []):
                analysis = session.get("training_analysis") or {}
                display = analysis.get("display_name")
                if display and display not in training_types:
                    training_types.append(display)
            entries.append({
                "date": key,
                "activity_state": activity_summary["activity_state"],
                "is_rest": activity_summary.get("is_rest_day", False),
                "duration": activity_summary.get("total_duration_min", 0) or 0,
                "distance": activity_summary.get("total_distance_km", 0) or 0,
                "load": activity_summary.get("total_training_load", 0) or 0,
                "sleep_h": sleep.get("total_hours", 0) or 0,
                "sleep_score": sleep.get("sleep_score"),
                "hrv": morning.get("hrv_ms"),
                "rhr": morning.get("resting_hr"),
                "bb": morning.get("body_battery_morning"),
                "recovery": recovery.get("overall_score"),
                "readiness": morning.get("training_readiness_score"),
                "training_types": training_types,
            })
        return entries

    def rebuild_index(self, category: str) -> None:
        return self.writer.rebuild_index(category)

    # 委托 Validator
    def validate(self, memory: Memory) -> list[str]:
        return self.validator.validate(memory)

    def integrity_check(self) -> dict[str, Any]:
        return self.validator.integrity_check()

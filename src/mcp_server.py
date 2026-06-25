"""MCP Server — 将 Rundown 数据暴露为 MCP Resources 和 Tools。

基于 fastmcp 框架，供 OpenClaw / Claude Desktop 连接。
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def create_server(config, auth, storage, memory_store, user_id: int):
    """创建并配置 FastMCP 服务器。

    暴露 Resources（只读上下文）和 Tools（AI 可调用的查询/分析函数）。
    """
    from fastmcp import FastMCP

    mcp = FastMCP(
        name="rundown",
        instructions="""你已接入 Rundown——一个 运动数据 + AI 跑步教练系统。

## 核心能力
- **每日综合报告**：包含昨日训练详情（分段配速、步频、功率、心率、触地时间）、昨夜睡眠质量、今晨恢复状态（HRV、静息心率、身体电量）、训练负荷（ACWR）、7日趋势、异常检测、今日训练建议。
- **训练细节分析**：每项活动的分段数据，包括配速变化、步频、功率、触地时间、步幅、垂直振幅、爬升等。
- **历史查询**：前30天的活动列表、健康指标趋势、任意日期的日报。
- **静态HTML报告**：可生成三主题（运动/清新/暗黑）完整HTML日报，包含趋势图，浏览器直接打开。

## 何时主动触发
- 用户提到"今天状态"、"昨天训练"、"睡眠"、"恢复"、"HRV"、"跑步数据"→ 读取 `rundown://daily/latest`
- 用户问"最近一周"、"趋势"、"负荷"、"训练量" → 读取 `rundown://context/full`
- 用户问"活动详情"、"配速"、"步频"、"功率"、"分段" → 调用 `get_activity_detail`
- 用户说"生成报告"、"日报"、"HTML" → 调用 `generate_report` 或 `generate_html_report`
- 用户说"截图"、"生成图片"、"分享"、"导出图片"、"打卡" → 调用 `generate_image`（可选 theme: fresh/sport/dark）
- 用户问"目标"、"5K"、"备赛"、"PB" → 读取 `rundown://goals/active`
- 用户要"更新资料"、"设置目标"、"输入身高体重" → 调用 `update_profile` 或 `set_goal`

## 典型对话示例
- 用户："早上好，今天状态怎么样？" → 你读取 daily/latest，用自然语言总结状态并给出训练建议
- 用户："帮我看看昨天那场跑步的技术数据" → 你先 query_activities 找到活动ID，再 get_activity_detail 获取分段
- 用户："这周跑量够不够？离目标还差多少？" → 你读取 goals/active + query_activities，计算对比
- 用户："生成今天的HTML日报" → 你调用 generate_html_report

## 数据时效
- 健康数据每日更新（需先运行 `rundown sync`）
- 日报每天早上自动生成
- 活动详情随时可查""",
    )

    # ═══════════════════════════════════════════════════════
    # Resources: 只读数据，自动注入 AI 上下文
    # ═══════════════════════════════════════════════════════

    @mcp.resource("rundown://daily/latest")
    def get_latest_daily() -> str:
        """【最常用】最新每日综合报告。包含：昨日训练详情（类型/时长/距离/配速/心率/负荷）、
        昨夜睡眠（时长/质量/深睡占比）、今晨恢复状态（HRV/静息心率/身体电量/训练准备）、
        训练负荷ACWR、7日趋势、异常提醒、今日训练建议、AI教练洞察。"""
        mem = memory_store.get_latest("daily_report")
        if mem is None:
            return "暂无日报，请先运行 rundown sync"
        return _format_memory(mem)

    @mcp.resource("rundown://daily/{target_date}")
    def get_daily_by_date(target_date: str) -> str:
        """指定日期的日报。"""
        mem = memory_store.get(target_date)
        if mem is None:
            return f"未找到 {target_date} 的日报"
        return _format_memory(mem)

    @mcp.resource("rundown://context/full")
    def get_full_context() -> str:
        """【全面分析时用】完整训练上下文包。包含：最新日报全文 + 前7天恢复/睡眠/训练趋势 +
        活跃训练目标 + 个人竞技档案。当用户问"最近一周"、"整体状态"、"趋势如何"时使用。"""
        parts = []

        # 最新日报
        mem = memory_store.get_latest("daily_report")
        if mem:
            parts.append(f"# 最新日报 ({mem.id})\n{_format_memory(mem)}")

        # 前 7 天摘要
        parts.append("\n# 前 7 天数据")
        for i in range(1, 8):
            d = date.today() - timedelta(days=i)
            m = memory_store.get(str(d))
            if m:
                fm = m.front_matter
                rec = fm.get("recovery", {})
                sleep = fm.get("last_night_sleep", {})
                ya = fm.get("yesterday_activities", {})
                dur = ya.get("total_duration_min", 0) if not ya.get("is_rest_day") else 0
                parts.append(
                    f"- {d}: 恢复 {rec.get('overall_score', '?')}/100 "
                    f"| 睡眠 {sleep.get('total_hours', '?')}h "
                    f"| 训练 {dur}min"
                )

        # 活跃目标
        goals = memory_store.list_by_type("goal", status="active")
        if goals:
            parts.append("\n# 活跃目标")
            for g in goals:
                fm = g.front_matter
                parts.append(
                    f"- **{g.id}**: {fm.get('metrics', {})}"
                )

        # 个人资料
        profile = memory_store.get("fitness-assessment")
        if profile:
            parts.append(f"\n# 个人资料\n{profile.body[:500]}")

        return "\n".join(parts)

    @mcp.resource("rundown://goals/active")
    def get_active_goals() -> str:
        """进行中的训练目标。"""
        goals = memory_store.list_by_type("goal", status="active")
        if not goals:
            return "暂无活跃目标"
        return "\n\n".join(
            f"# {g.id}\n{g.body[:300]}" for g in goals
        )

    @mcp.resource("rundown://profile")
    def get_profile() -> str:
        """个人竞技档案。"""
        mem = memory_store.get("fitness-assessment")
        if mem is None:
            return "暂无档案，请运行 rundown setup"
        return _format_memory(mem)

    @mcp.resource("rundown://preferences")
    def get_preferences() -> str:
        """训练偏好。"""
        mem = memory_store.get("preferences")
        if mem is None:
            return "暂无偏好设置"
        return _format_memory(mem)

    # ═══════════════════════════════════════════════════════
    # Tools: AI 可调用的查询/分析函数
    # ═══════════════════════════════════════════════════════

    @mcp.tool()
    def query_activities(days: int = 7) -> str:
        """查询最近 N 天的活动列表（含距离、时长、心率、负荷）。"""
        activities = storage.get_recent_activities(user_id, days)
        if not activities:
            return "暂无活动数据"
        lines = [f"最近 {days} 天活动 ({len(activities)} 条):"]
        for a in activities:
            dur = (a.get("duration_seconds", 0) or 0) / 60
            dist = (a.get("distance_meters", 0) or 0) / 1000
            lines.append(
                f"- {a.get('activity_date', '?')}: {a.get('activity_name', '?')} "
                f"{dur:.0f}min {dist:.1f}km HR{a.get('avg_heart_rate', '?')} "
                f"load{a.get('training_load', 0)}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def query_health_metrics(days: int = 7) -> str:
        """查询最近 N 天的健康指标（睡眠、HRV、心率、身体电量等）。"""
        metrics = storage.get_health_metrics_range(user_id, days)
        if not metrics:
            return "暂无健康数据"
        lines = [f"最近 {days} 天健康指标:"]
        for m in metrics:
            lines.append(
                f"- {m.get('metric_date', '?')}: "
                f"睡眠 {m.get('sleep_duration_hours', '?')}h "
                f"RHR {m.get('resting_heart_rate', '?')} "
                f"HRV {m.get('hrv_last_night_avg', '?')}ms "
                f"电量 {m.get('body_battery_high', '?')} "
                f"准备 {m.get('training_readiness_score', '?')}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def get_activity_detail(activity_id: str) -> str:
        """获取单条活动的详细技术数据。包含：每公里/分段配速、步频变化、功率输出（平均/标准化）、
        触地时间(ms)、步幅(cm)、垂直振幅(mm)、心率区间、爬升、训练效果标签。
        用户问"配速"、"步频"、"功率"、"技术分析"、"分段数据"时调用。
        需要先通过 query_activities 获取 activity_id。"""
        from .activity import get_activity_splits, get_activity_detail
        detail = get_activity_detail(storage, activity_id)
        splits = get_activity_splits(storage, activity_id)
        if not detail:
            return f"未找到活动 {activity_id}"
        lines = [
            f"活动: {detail.get('activityName', '?')}",
            f"距离: {(detail.get('summaryDTO', {}).get('distance', 0) or 0)/1000:.1f}km",
            f"时长: {(detail.get('summaryDTO', {}).get('duration', 0) or 0)/60:.0f}min",
        ]
        for s in splits:
            dist = (s.get("distance_m") or 0) / 1000
            pace = s.get("pace_per_km")
            pace_str = f"{int(pace//60)}:{int(pace%60):02d}/km" if pace else "—"
            lines.append(
                f"  {dist:.1f}km {pace_str} "
                f"HR{s.get('avg_hr', '?')} cad{s.get('avg_cadence', '?')}"
            )
        return "\n".join(lines)

    @mcp.tool()
    def search_memories(keyword: str) -> str:
        """全文搜索记忆库。"""
        results = memory_store.search(keyword)
        if not results:
            return f"未找到包含 '{keyword}' 的记忆"
        lines = [f"搜索 '{keyword}' 找到 {len(results)} 条:"]
        for m in results[:10]:
            lines.append(f"- [{m.type.value}] {m.id}: {m.body[:100]}...")
        return "\n".join(lines)

    @mcp.tool()
    def generate_image(target_date: str = "", theme: str = "fresh") -> str:
        """将指定日期的 HTML 日报截图导出为 PNG 图片。路径 output/{date}.png。
        theme: fresh(清新默认) | sport(运动橙) | dark(暗黑)。
        用户说"生成图片"、"截图"、"分享"、"导出图片"时调用。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        mem = memory_store.get(str(d))
        if mem is None:
            mem = memory_store.generate_daily_report(str(user_id), d)

        from .image import render_daily_image
        out = f"output/{d}.png"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        png_path = render_daily_image(mem, output_path=out, theme=theme)
        return f"✅ PNG 已生成: {png_path} (theme={theme})"

    @mcp.tool()
    def get_training_advice() -> str:
        """基于最新日报生成训练建议。"""
        mem = memory_store.get_latest("daily_report")
        if mem is None:
            return "暂无日报数据"
        fm = mem.front_matter
        rec = fm.get("recommendation", {})
        ai = fm.get("ai_insight", {})
        lines = [
            f"训练建议 ({mem.id}):",
            f"强度: {rec.get('intensity', '?')}",
            f"建议: {rec.get('training_advice', '?')}",
        ]
        if ai:
            lines.append(f"\nAI 洞察: {ai.get('conclusion', '')}")
            for obs in ai.get("observations", []):
                lines.append(f"  • {obs}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════
    # Write Tools: AI 可以写入和触发操作
    # ═══════════════════════════════════════════════════════

    @mcp.tool()
    def update_profile(
        height_cm: int = 0,
        weight_kg: int = 0,
        age: int = 0,
        gender: str = "",
        location: str = "",
        pb_5k: str = "",
        pb_10k: str = "",
        pb_hm: str = "",
        pb_marathon: str = "",
    ) -> str:
        """更新个人基本资料和最佳成绩。留空的字段保持原值不变。"""
        from datetime import datetime
        from .memory import build_memory_file

        # 读取现有资料
        existing = memory_store.get("fitness-assessment")
        existing_fm = existing.front_matter if existing else {}
        existing_info = existing_fm.get("personal_info", {})
        existing_pb = existing_fm.get("personal_bests", {})

        # 合并
        info = {
            "height_cm": height_cm or existing_info.get("height_cm"),
            "weight_kg": weight_kg or existing_info.get("weight_kg"),
            "age": age or existing_info.get("age"),
            "gender": gender or existing_info.get("gender", ""),
            "location": location or existing_info.get("location", ""),
        }
        pbs = dict(existing_pb)
        if pb_5k: pbs["5k"] = {"time": pb_5k}
        if pb_10k: pbs["10k"] = {"time": pb_10k}
        if pb_hm: pbs["half_marathon"] = {"time": pb_hm}
        if pb_marathon: pbs["marathon"] = {"time": pb_marathon}

        fm = {
            "type": "fitness_profile",
            "profile_type": "assessment",
            "updated": datetime.now().isoformat(timespec="seconds"),
            "personal_info": info,
            "personal_bests": pbs,
            "tags": ["fitness-profile", str(date.today().year)],
        }

        body_parts = [
            "# 竞技档案",
            f"\n## 基本信息",
            f"- 身高: {info['height_cm'] or '?'} cm",
            f"- 体重: {info['weight_kg'] or '?'} kg",
            f"- 年龄: {info['age'] or '?'}",
        ]
        if info.get("gender"): body_parts.append(f"- 性别: {info['gender']}")
        if info.get("location"): body_parts.append(f"- 地点: {info['location']}")

        body_parts.append("\n## 个人最佳")
        for dist, data in pbs.items():
            if isinstance(data, dict) and data.get("time"):
                body_parts.append(f"- **{dist}**: {data['time']}")

        body = "\n".join(body_parts)
        path = Path(config.memory_dir) / "profile" / "fitness-assessment.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_memory_file(fm, body), encoding="utf-8")
        logger.info("Profile updated via MCP")
        return f"✅ 个人资料已更新。身高 {info['height_cm']}cm 体重 {info['weight_kg']}kg，最佳: {list(pbs.keys())}"

    @mcp.tool()
    def set_goal(
        name: str,
        distance: str,
        target_time: str,
        target_date: str = "",
        weekly_km: int = 50,
    ) -> str:
        """创建或更新训练目标。"""
        from datetime import datetime
        from .memory import build_memory_file

        if not target_date:
            target_date = str(date.today().replace(year=date.today().year + 1))

        goal_id = f"goal-{date.today().year}-{distance}"
        fm = {
            "type": "goal",
            "id": goal_id,
            "goal_type": "time_based",
            "category": "running",
            "status": "active",
            "priority": "high",
            "created": str(date.today()),
            "target_date": target_date,
            "review_cycle": "weekly",
            "metrics": {
                f"target_{distance}": target_time,
                "weekly_mileage_km": weekly_km,
            },
            "tags": [distance, str(date.today().year), "active"],
        }
        body = f"""# {name}

## 目标
- 距离: {distance}
- 目标成绩: {target_time}
- 截止日期: {target_date}
- 周跑量: {weekly_km} km

## 进度
创建于 {date.today()}。
"""

        path = Path(config.memory_dir) / "goals" / "active" / f"{goal_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_memory_file(fm, body), encoding="utf-8")
        logger.info("Goal created via MCP: %s", goal_id)
        return f"✅ 目标已创建: {name} — {distance} {target_time} (截止 {target_date})"

    @mcp.tool()
    def generate_report(target_date: str = "") -> str:
        """生成指定日期的运动日报（默认今天）。用户说"生成日报"、"帮我看看今天的报告"、
        "分析一下昨天的训练"时调用。成功后返回训练/睡眠/恢复/HRV的摘要数据。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        mem = memory_store.generate_daily_report(str(user_id), d)
        fm = mem.front_matter
        ya = fm.get("yesterday_activities", {})
        sleep = fm.get("last_night_sleep", {})
        rec = fm.get("recovery", {})

        return (
            f"✅ 日报已生成: {d}\n"
            f"训练: {ya.get('day_type', 'rest')} "
            f"{ya.get('total_duration_min', 0)}min "
            f"{ya.get('total_distance_km', 0):.1f}km\n"
            f"睡眠: {sleep.get('total_hours', '?')}h {sleep.get('quality', '?')}\n"
            f"恢复: {rec.get('overall_score', '?')}/100 {rec.get('level', '?')}\n"
            f"HRV: {fm.get('this_morning', {}).get('hrv_ms', '?')}ms "
            f"RHR: {fm.get('this_morning', {}).get('resting_hr', '?')}"
        )

    @mcp.tool()
    def generate_html_report(target_date: str = "") -> str:
        """生成静态 HTML 运动日报到 output/ 目录。绿黑色潮流风格，包含状态面板、
        训练卡片、ACWR可视化、SVG趋势图、AI洞察。浏览器直接打开，无需服务器。
        用户说"生成HTML"、"导出日报"、"给我一个网页版"时调用。"""
        if target_date:
            d = date.fromisoformat(target_date)
        else:
            d = date.today()

        # 确保日报存在
        mem = memory_store.get(str(d))
        if mem is None:
            mem = memory_store.generate_daily_report(str(user_id), d)

        from .render import render_daily_html
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{d}.html")
        render_daily_html(mem, output_path)
        return f"✅ HTML 日报已生成: {output_path}"

    return mcp


def _format_memory(mem) -> str:
    """格式化 Memory 为文本。"""
    fm = mem.front_matter
    parts = [mem.body]

    # 附加关键 Front Matter 数据
    ya = fm.get("yesterday_activities", {})
    if ya and not ya.get("is_rest_day"):
        parts.append(
            f"\n数据: {ya.get('total_duration_min', 0)}min "
            f"{ya.get('total_distance_km', 0):.1f}km "
            f"负荷 {ya.get('total_training_load', 0)}"
        )

    rec = fm.get("recovery", {})
    if rec:
        parts.append(f"恢复: {rec.get('overall_score', '?')}/100 {rec.get('level', '?')}")

    return "\n".join(parts)

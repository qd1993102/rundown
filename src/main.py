"""CLI 入口模块 — 命令行参数解析与流程编排。

Rundown — Your daily running rundown.

Commands:
    rundown sync        同步数据 + 生成日报和记忆摘要
    rundown daily       查看/生成每日综合报告
    rundown activities  查询活动列表
    rundown health      查询健康指标
    rundown memory      记忆管理（list/show/summarize/goal/plan/...）
    rundown status      查看同步状态
    rundown mcp         启动 MCP Server
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .config import get_config, ConfigError
from .auth import AuthManager
from .fetcher import Fetcher
from .storage import Storage
from .memory import MemoryStore, MemoryType, MemoryStatus
from .render import render_daily_html
from .image import render_daily_image

logger = logging.getLogger(__name__)
console = Console()


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _setup(config=None):
    """初始化所有模块并返回 (auth, fetcher, storage, memory_store, user_id)。"""
    if config is None:
        config = get_config()

    auth = AuthManager(config)
    fetcher = Fetcher(auth)
    storage = Storage(config)
    def _make_api_client():
        from garmy import APIClient
        return APIClient(auth_client=auth.client)

    memory_store = MemoryStore(
        config.memory_dir,
        db_getter=lambda: storage.db,
        api_client_getter=_make_api_client,
    )

    # 获取 user_id（从已认证的 client 中获取，garmy 2.0 需要 int）
    user_id = _get_user_id(auth)

    return config, auth, fetcher, storage, memory_store, user_id


def _get_user_id(auth: AuthManager) -> int:
    """获取当前用户的运动平台 user_id (int)。

    通过 APIClient.profile 获取真实 user_id。
    """
    try:
        from garmy import APIClient
        api = APIClient(auth_client=auth.client)
        profile = api.profile
        if isinstance(profile, dict):
            uid = profile.get("id")
            if uid:
                user_id = int(uid)
                logger.info("user_id: %d", user_id)
                return user_id
    except Exception as exc:
        logger.warning("获取 user_id 失败: %s", exc)
    raise RuntimeError("无法获取运动平台 user_id，请检查账号配置")


def _get_ai_insight(fm: dict[str, Any], target_date: date,
                    memory_store=None) -> dict[str, Any] | None:
    """调用 DeepSeek API 获取 AI 教练洞察。"""
    try:
        from .coach import get_coach_insight
        return get_coach_insight(fm, target_date, memory_store=memory_store)
    except Exception as exc:
        logger.warning("AI 洞察生成失败: %s", exc)
        return None


def _parse_date(date_str: str) -> date:
    """解析日期字符串 (YYYY-MM-DD)。"""
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无效日期格式: {date_str}，应为 YYYY-MM-DD")


# ═══════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════

def cmd_sync(args: argparse.Namespace) -> None:
    """sync 命令：同步数据 + 生成记忆。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    console.print(Panel.fit(
        "[bold blue]🔄 Rundown Sync[/]\n同步运动数据并生成记忆",
        border_style="blue",
    ))

    # 确定日期范围
    if args.from_date and args.to_date:
        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date)
    elif args.full:
        start = date.today() - timedelta(days=365 * 3)  # 3 years
        end = date.today()
    else:
        days = args.days or config.sync_days
        start = date.today() - timedelta(days=days)
        end = date.today()

    console.print(f"📅 同步范围: {start} ~ {end}")

    # 执行同步
    try:
        result = storage.sync_range(user_id, start, end, args.metrics)
        console.print(f"[green]✅ 同步完成[/]")
        if result:
            for k, v in result.items():
                console.print(f"  {k}: {v}")
    except Exception as exc:
        console.print(f"[red]❌ 同步失败: {exc}[/]")
        if not args.no_memory:
            console.print("[yellow]⚠️  跳过记忆生成[/]")
            return

    # 生成记忆
    if not args.no_memory:
        console.print("\n[bold]🧠 生成记忆...[/]")
        try:
            # 日报
            daily = memory_store.generate_daily_report(user_id)
            console.print(f"  📰 日报: {daily.id}")

            # 周摘要
            summary = memory_store.generate_weekly_summary(user_id)
            console.print(f"  📊 周摘要: {summary.id}")

            # 恢复摘要
            recovery = memory_store.generate_recovery_summary(user_id)
            console.print(f"  💤 恢复摘要: {recovery.id}")

            # 重建索引
            for cat in ["daily", "summaries", "recovery", "execution"]:
                memory_store.rebuild_index(f"auto/{cat}")

            console.print("[green]✅ 记忆生成完成[/]")
        except Exception as exc:
            console.print(f"[red]❌ 记忆生成失败: {exc}[/]")


def cmd_daily(args: argparse.Namespace) -> None:
    """daily 命令：查看/生成每日综合报告。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    target = _parse_date(args.date) if args.date else date.today()
    need_ai = getattr(args, 'ai', False)
    want_image = getattr(args, 'image', False) or getattr(args, 'html', False)

    if args.format == "json":
        # JSON 输出
        import json
        mem = memory_store.get(str(target))
        if mem is None and target == date.today():
            mem = memory_store.generate_daily_report(user_id, target)
        if mem is None:
            console.print(f"[red]未找到 {target} 的日报[/]")
            return
        if need_ai:
            ai_result = _get_ai_insight(mem.front_matter, target, memory_store=memory_store)
            if ai_result:
                mem.front_matter['ai_insight'] = ai_result
        console.print_json(json.dumps(mem.front_matter, ensure_ascii=False, indent=2, default=str))
        return

    if args.html:
        # HTML 输出到统一目录 output/YYYY-MM-DD.html
        console.print("[yellow]📰 正在生成日报...[/]")
        mem = memory_store.generate_daily_report(user_id, target)
        if mem is None:
            console.print(f"[red]未找到 {target} 的日报，请先运行 rundown sync[/]")
            return
        if need_ai:
            ai_result = _get_ai_insight(mem.front_matter, target, memory_store=memory_store)
            if ai_result:
                mem.front_matter['ai_insight'] = ai_result
                mem.save()
        # 统一输出目录: output/YYYY-MM-DD.html
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(output_dir / f"{target}.html")
        render_daily_html(mem, output_path)
        console.print(f"[green]✅ HTML 日报已生成: {output_path}[/]")
        console.print(f"[dim]用浏览器打开即可查看[/]")
        if getattr(args, 'image', False):
            theme = getattr(args, 'theme', 'sport')
            out_png = str(output_dir / f"{target}.png")
            png_path = render_daily_image(mem, output_path=out_png, theme=theme)
            console.print(f"[green]🖼️  PNG 已生成: {png_path}[/]")
        return

    # ── 生成图片/HTML 前先补齐数据 ──
    if want_image:
        # 补齐 target 前一天到今天的数据（确保活动和健康指标是新的）
        from_day = target - timedelta(days=2)
        console.print(f"[dim]🔄 补齐数据: {from_day} ~ {target}[/]")
        try:
            # 先清理 pending 状态的记录，强制重新拉取
            storage.reset_pending_metrics(user_id, from_day, target)
            result = storage.sync_range(user_id, from_day, target)
            new_count = result.get('completed', 0)
            if new_count > 0:
                console.print(f"[green]  ✅ 新拉取 {new_count} 条数据[/]")
            else:
                console.print(f"[dim]  📦 数据已是最新[/]")
        except Exception as exc:
            console.print(f"[yellow]  ⚠️ 数据补齐部分失败: {exc}[/]")

    if args.image:
        # 直接截图模式：重新生成日报（使用最新数据）
        console.print("[yellow]📰 正在生成日报...[/]")
        mem = memory_store.generate_daily_report(user_id, target)
        if mem is None:
            console.print(f"[red]未找到 {target} 的日报[/]")
            return
        if need_ai:
            ai_result = _get_ai_insight(mem.front_matter, target, memory_store=memory_store)
            if ai_result:
                mem.front_matter['ai_insight'] = ai_result
                mem.save()
        theme = getattr(args, 'theme', 'sport')
        output_dir = Path("output")
        output_dir.mkdir(parents=True, exist_ok=True)
        out = str(output_dir / f"{target}.png")
        png_path = render_daily_image(mem, output_path=out, theme=theme)
        console.print(f"[green]🖼️  PNG 已生成: {png_path}[/]")
        return

    # 尝试读取已有日报
    mem = memory_store.get(str(target))

    # 没有则自动生成（今天或过去日期都支持）
    if mem is None:
        console.print(f"[yellow]📰 正在生成 {target} 的日报...[/]")
        mem = memory_store.generate_daily_report(user_id, target)

    if mem is None:
        console.print(f"[red]无法生成 {target} 的日报，请先运行 rundown sync[/]")
        return

    # --ai 标志：调用 DeepSeek 刷新 AI 洞察（传入 memory_store 收集历史上下文）
    if need_ai:
        ai_result = _get_ai_insight(mem.front_matter, target, memory_store=memory_store)
        if ai_result:
            mem.front_matter['ai_insight'] = ai_result
            mem.save()
            console.print("[green]🤖 DeepSeek AI 洞察已生成（含前7天趋势 + 活跃目标）[/]")

    # Rich 美化输出
    fm = mem.front_matter
    ya = fm.get("yesterday_activities", {})
    sleep = fm.get("last_night_sleep", {})
    morning = fm.get("this_morning", {})
    load = fm.get("training_load", {})
    recovery = fm.get("recovery", {})
    rec = fm.get("recommendation", {})
    anomalies = fm.get("anomalies", {})
    goals = fm.get("goal_progress", {})

    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    wd = weekday_names[target.weekday()]

    console.print()
    console.rule(f"[bold blue]📰 每日训练报告 — {target} {wd}[/]")

    # 昨日训练
    if ya.get("is_rest_day"):
        steps = ya.get("daily_steps", 0)
        dist = ya.get("daily_distance_km", 0)
        active_cal = ya.get("daily_active_cal", 0)
        level = ya.get("activity_level", "")
        level_emoji = {"very_active": "🔥", "active": "🟢", "light": "🟡", "sedentary": "⚪"}
        le = level_emoji.get(level, "")
        console.print(f"\n[bold]🏃 今日训练[/]: [dim]休息日（无正式记录）[/]")
        console.print(f"   [dim]全天活动: {steps} 步 | {dist} km | 活动消耗 {active_cal} cal {le} {level}[/]")
    else:
        total_dist = ya.get('total_distance_km', 0)
        console.print(f"\n[bold]🏃 今日训练[/]: {ya.get('day_type', '?')} | "
                      f"{ya.get('total_duration_min', 0)}min | "
                      f"{total_dist:.1f}km | "
                      f"负荷 {ya.get('total_training_load', 0)}")
        # 逐条显示
        for s in ya.get('sessions', []):
            d_km = s.get('distance_km') or 0
            console.print(f"   [dim]{s['type']}: {s['name']} | {s['duration_min']}min"
                          f"{' | ' + str(d_km) + 'km' if d_km else ''}"
                          f" | HR {s.get('avg_hr', '?')}"
                          f" | load {s.get('training_load', 0)}[/]")

    # 训练细节分析
    session_analyses = fm.get("session_analyses", [])
    if session_analyses:
        console.print(f"\n[bold]🔬 训练细节分析[/]")
        for analysis_text in session_analyses:
            # 提取关键行（非标题和非分隔符的行）
            for line in analysis_text.split("\n"):
                stripped = line.strip()
                if stripped and not stripped.startswith("###") and not stripped.startswith("**分段"):
                    console.print(f"  [dim]{stripped}[/]")

    # 睡眠
    quality_emoji = {"excellent": "🟢", "good": "🟢", "fair": "🟡", "poor": "🔴"}
    qe = quality_emoji.get(sleep.get("quality", ""), "")
    console.print(f"[bold]😴 睡眠[/]: {sleep.get('total_hours', '?')}h | "
                  f"评分 {sleep.get('sleep_score', '?')} {qe} {sleep.get('quality', '?')}")

    # 今晨状态
    console.print(f"\n[bold]📊 今晨状态[/]")
    status_table = Table(show_header=False, box=None, padding=(0, 2))
    status_table.add_column(style="dim")
    status_table.add_column()
    status_table.add_row("静息心率", f"{morning.get('resting_hr', '?')} bpm")
    status_table.add_row("HRV", f"{morning.get('hrv_ms', '?')} ms ({morning.get('hrv_status', '?')})")
    status_table.add_row("身体电量", str(morning.get('body_battery_morning', '?')))
    status_table.add_row("训练准备", str(morning.get('training_readiness_score', '?')))
    console.print(status_table)

    # 训练负荷
    acwr_emoji = {
        "optimal": "🟢", "undertraining": "🟡",
        "overreaching": "🟠", "high_risk": "🔴",
    }
    ae = acwr_emoji.get(load.get("acwr_status", ""), "")
    console.print(f"\n[bold]📈 训练负荷[/]: ACWR {load.get('acwr', '?')} "
                  f"{ae} {load.get('acwr_status', '?')} | "
                  f"恢复评分 {recovery.get('overall_score', '?')}/100 "
                  f"({recovery.get('level', '?')})")

    # 今日建议
    ready_emoji = "✅" if rec.get("ready_to_train") else "❌"
    console.print(f"\n[bold]🎯 今日训练[/] {ready_emoji} {rec.get('training_advice', '?')} "
                  f"(强度: {rec.get('intensity', '?')})")
    for c in rec.get("caution", []):
        console.print(f"  ⚠️  {c}")

    # 异常提醒
    if anomalies.get("items"):
        console.print(f"\n[bold red]⚠️ 异常提醒[/] ({anomalies.get('level', '?')})")
        for item in anomalies["items"]:
            console.print(f"  • [{item['severity']}] {item['message']}")

    # AI 教练洞察
    ai = fm.get("ai_insight", {})
    if ai:
        console.print(f"\n[bold green]🤖 AI 教练洞察[/]")
        console.print(f"  [bold white]{ai.get('conclusion', '')}[/]")
        if ai.get("observations"):
            for obs in ai["observations"]:
                console.print(f"  [dim]• {obs}[/]")
        if ai.get("warnings"):
            for w in ai["warnings"]:
                console.print(f"  [yellow]⚠ {w}[/]")
        if ai.get("recommendations"):
            for r in ai["recommendations"]:
                console.print(f"  [green]→ {r}[/]")

    # 目标进度
    active_goals = goals.get("active_goals", [])
    if active_goals:
        console.print(f"\n[bold]🏁 目标进度[/]")
        for g in active_goals:
            on_track = "🟢" if g.get("on_track") else "🟡"
            console.print(f"  {on_track} {g.get('name', '?')}: "
                          f"{g.get('current_best', '?')} → {g.get('target', '?')} "
                          f"({g.get('weeks_remaining', '?')} 周)")

    console.rule()


def cmd_activities(args: argparse.Namespace) -> None:
    """activities 命令：查询活动列表。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    if args.type:
        activities = fetcher.get_activities_by_type(args.type, args.recent or 30)
    else:
        activities = fetcher.get_activities_recent(args.recent or 30)

    if args.export:
        storage.export_csv(activities, args.export)
        console.print(f"[green]✅ 已导出到 {args.export}[/]")
        return

    # Rich 表格展示
    table = Table(title=f"🏃 最近 {args.recent or 30} 天活动")
    table.add_column("日期", style="dim")
    table.add_column("类型")
    table.add_column("名称")
    table.add_column("时长", justify="right")
    table.add_column("距离", justify="right")
    table.add_column("心率", justify="right")
    table.add_column("负荷", justify="right")

    for a in activities[:50]:
        dur = (a.get("duration", 0) or 0) / 60
        dist = (a.get("distance", 0) or 0) / 1000
        table.add_row(
            str(a.get("start_time_local", ""))[:10],
            a.get("activity_type_name", ""),
            str(a.get("activity_name", ""))[:20],
            f"{dur:.0f}min",
            f"{dist:.1f}km" if dist else "",
            str(a.get("average_hr", "")),
            str(a.get("activity_training_load", "")),
        )

    console.print(table)
    console.print(f"[dim]共 {len(activities)} 条记录[/]")


def cmd_health(args: argparse.Namespace) -> None:
    """health 命令：查询健康指标。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    if args.metric:
        metrics = [args.metric]
    else:
        metrics = ["sleep", "hrv", "heart_rate", "stress", "body_battery"]

    table = Table(title=f"💊 健康指标 (最近 {args.days or 7} 天)")
    table.add_column("日期", style="dim")
    for m in metrics:
        table.add_column(m, justify="right")

    today = date.today()
    for i in range(args.days or 7):
        target = today - timedelta(days=i)
        row = [str(target)]
        for m in metrics:
            data = fetcher.get_health_metric(m, target)
            if data is None:
                row.append("—")
            elif m == "sleep":
                dur = (data.get("sleep_duration", 0) or 0) / 3600
                row.append(f"{dur:.1f}h")
            elif m == "hrv":
                row.append(str(data.get("hrv_avg", "—")))
            elif m == "heart_rate":
                row.append(str(data.get("resting_heart_rate", "—")))
            elif m == "stress":
                row.append(str(data.get("avg_stress", "—")))
            elif m == "body_battery":
                row.append(str(data.get("body_battery_highest", "—")))
            else:
                row.append("✓")
        table.add_row(*row)

    console.print(table)


def cmd_memory(args: argparse.Namespace) -> None:
    """memory 命令：记忆管理。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    sub = args.memory_subcommand

    if sub == "list":
        mem_type = MemoryType(args.type) if args.type else None
        mem_status = MemoryStatus(args.status) if args.status else None
        tags = args.tag.split(",") if args.tag else None

        if args.search:
            memories = memory_store.search(args.search)
        elif mem_type:
            memories = memory_store.list_by_type(mem_type, status=mem_status, tags=tags)
        else:
            memories = memory_store.query(tags=tags)

        table = Table(title="🧠 记忆列表")
        table.add_column("ID", style="dim")
        table.add_column("类型")
        table.add_column("日期")
        table.add_column("标签")

        for m in memories[:50]:
            table.add_row(
                m.id,
                m.type.value,
                str(m.created_date or ""),
                ", ".join(m.tags[:5]),
            )

        console.print(table)
        console.print(f"[dim]共 {len(memories)} 条记忆[/]")

    elif sub == "show":
        mem = memory_store.get(args.memory_id)
        if mem is None:
            console.print(f"[red]未找到记忆: {args.memory_id}[/]")
            return
        console.print(Panel(mem.body, title=f"🧠 {mem.id} ({mem.type.value})"))
        console.print("[dim]Front Matter:[/]")
        console.print_json(
            __import__("json").dumps(mem.front_matter, ensure_ascii=False, indent=2, default=str)
        )

    elif sub == "summarize":
        period = args.period or "weekly"
        if args.date:
            target = _parse_date(args.date)
        else:
            target = date.today()

        console.print(f"[bold]生成 {period} 摘要...[/]")
        summary = memory_store.generate_weekly_summary(user_id, target)
        console.print(f"[green]✅ 摘要已生成: {summary.id}[/]")

        recovery = memory_store.generate_recovery_summary(user_id, target)
        console.print(f"[green]✅ 恢复摘要已生成: {recovery.id}[/]")

        for cat in ["summaries", "recovery"]:
            memory_store.rebuild_index(f"auto/{cat}")

    elif sub == "check":
        console.print("[bold]🔍 执行完整性检查...[/]")
        result = memory_store.integrity_check()

        console.print(f"\n检查文件: {result['checked']}")
        console.print(f"  [green]通过: {result['pass_count']}[/]")
        console.print(f"  [yellow]警告: {result['warn_count']}[/]")
        console.print(f"  [red]错误: {result['error_count']}[/]")

        if result["issues"]:
            console.print("\n[bold]详情:[/]")
            for issue in result["issues"]:
                style = "red" if issue["level"] == "error" else "yellow"
                console.print(f"  [{style}][{issue['level']}][/] {issue['file']}: {issue['message']}")

    elif sub == "index":
        cats = ["daily", "summaries", "recovery", "execution"]
        for cat in cats:
            memory_store.rebuild_index(f"auto/{cat}")
        console.print("[green]✅ 所有索引已重建[/]")

    else:
        console.print(f"[red]未知 memory 子命令: {sub}[/]")


def cmd_status(args: argparse.Namespace) -> None:
    """status 命令：查看同步状态。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    status_list = storage.get_all_sync_status(user_id)

    if not status_list:
        console.print("[yellow]暂无同步记录，请先运行 rundown sync[/]")
        return

    table = Table(title="📡 同步状态")
    table.add_column("日期")
    table.add_column("指标")
    table.add_column("状态")

    for s in status_list[-30:]:
        table.add_row(
            str(s.get("date", "")),
            str(s.get("metric", "")),
            str(s.get("status", "")),
        )

    console.print(table)


def cmd_init(args: argparse.Namespace) -> None:
    """init 命令：引导式创建配置文件。"""
    console.rule("[bold green]🚀 Rundown Init[/]")
    console.print("首次使用？让我帮你创建配置文件。\n")

    # 1. 选择 Provider
    provider = _ask("运动平台 (garmin/coros)", "garmin")
    if provider not in ("garmin", "coros"):
        console.print("[red]无效的平台，请输入 garmin 或 coros[/]")
        return

    # 2. 账号
    if provider == "coros":
        hint = "邮箱或手机号"
    else:
        hint = "Garmin Connect 邮箱"
    account = _ask(f"账号 ({hint})")
    if not account:
        console.print("[red]账号不能为空[/]")
        return

    # 3. 密码
    password = _ask("密码")
    if not password:
        console.print("[red]密码不能为空[/]")
        return

    # 4. 存储位置
    console.print("\n[dim]数据存储位置（回车使用默认）[/]")
    location = _ask("数据库路径", "./data/rundown_data.db")
    sync_days = _ask("默认同步天数", "30")

    env_content = f"""# Rundown 配置
RUNDOWN_PROVIDER={provider}
RUNDOWN_ACCOUNT={account}
RUNDOWN_PASSWORD={password}
RUNDOWN_DB_PATH={location}
RUNDOWN_SYNC_DAYS={sync_days}
RUNDOWN_LOG_LEVEL=INFO
"""
    if provider == "garmin":
        domain = _ask("Garmin 区域 (garmin.com/garmin.cn)", "garmin.com")
        env_content += f"GARMIN_DOMAIN={domain}\n"

    # 写入
    env_path = Path(".env")
    if env_path.exists():
        overwrite = _ask(f"{env_path} 已存在，覆盖？(y/n)", "n")
        if overwrite.lower() != "y":
            console.print("[yellow]已取消[/]")
            return

    env_path.write_text(env_content)
    console.print(f"\n[green]✅ 配置已写入: {env_path}[/]")

    # 询问全局配置
    make_global = _ask("同时写入全局配置 ~/.rundown/.env？(y/n)", "y")
    if make_global.lower() == "y":
        global_dir = Path.home() / ".rundown"
        global_dir.mkdir(parents=True, exist_ok=True)
        (global_dir / ".env").write_text(env_content)
        console.print(f"[green]✅ 全局配置已写入: {global_dir / '.env'}[/]")

    # 询问首次同步
    do_sync = _ask("\n是否立即同步数据？(y/n)", "y")
    if do_sync.lower() == "y":
        console.print("\n[bold]开始首次同步...[/]")
        # 构造一个简单的 args namespace
        class SyncArgs:
            from_date = None
            to_date = None
            full = False
            days = int(sync_days) if sync_days.isdigit() else 30
            metrics = None
            no_memory = False
        cmd_sync(SyncArgs())


def cmd_setup(args: argparse.Namespace) -> None:
    """setup 命令：交互式录入个人资料、最佳成绩和目标。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    from datetime import datetime
    from src.memory import build_memory_file

    console.rule("[bold green]⚙️  Rundown Setup[/]")
    console.print("输入你的基本信息（回车跳过可留空）\n")

    # ── 1. 基本信息 ──
    console.print("[bold]1/4 基本信息[/]")
    height = _ask("身高 (cm)", "175")
    weight = _ask("体重 (kg)", "70")
    age = _ask("年龄", "30")
    gender = _ask("性别 (male/female)", "male")
    location = _ask("训练地点", "")

    # ── 2. 个人最佳成绩 ──
    console.print("\n[bold]2/4 个人最佳成绩[/]")
    pb_5k = _ask("5K 最佳 (格式 MM:SS 或留空)", "")
    pb_10k = _ask("10K 最佳", "")
    pb_hm = _ask("半马最佳", "")
    pb_marathon = _ask("全马最佳", "")
    vo2max = _ask("VO2max 估算值", "")

    # ── 3. 当前目标 ──
    console.print("\n[bold]3/4 训练目标[/]")
    has_goal = _ask("是否设置目标？(y/n)", "y")
    if has_goal.lower() == "y":
        goal_name = _ask("目标名称", "5K 突破")
        goal_distance = _ask("目标距离 (5k/10k/hm/marathon)", "5k")
        goal_time = _ask("目标成绩", "")
        goal_date_str = _ask("目标日期 (YYYY-MM-DD)", str(date.today().replace(year=date.today().year + 1)))
        goal_weekly_km = _ask("目标周跑量 (km)", "50")
    else:
        goal_name = goal_distance = goal_time = goal_date_str = goal_weekly_km = ""

    # ── 4. 训练偏好 ──
    console.print("\n[bold]4/4 训练偏好[/]")
    pref_time = _ask("偏好训练时间 (morning/afternoon/evening)", "evening")
    pref_workouts = _ask("偏好训练类型 (逗号分隔: 间歇,节奏,长距离,轻松跑)", "间歇,节奏,长距离")
    pref_terrain = _ask("偏好地形 (田径场/公路/混合)", "公路")
    injury_history = _ask("伤病史 (简要描述，无则留空)", "")
    training_philosophy = _ask("训练理念 (一句话)", "数据驱动，极化训练，重视恢复")

    # ── 写入文件 ──
    import yaml

    # Profile
    profile_fm = {
        "type": "fitness_profile",
        "profile_type": "assessment",
        "updated": datetime.now().isoformat(timespec="seconds"),
        "personal_info": {
            "height_cm": int(height) if height.isdigit() else None,
            "weight_kg": int(weight) if weight.isdigit() else None,
            "age": int(age) if age.isdigit() else None,
            "gender": gender,
            "location": location,
        },
        "personal_bests": {},
        "tags": ["fitness-profile", str(date.today().year)],
    }
    if pb_5k:
        profile_fm["personal_bests"]["5k"] = {"time": pb_5k}
    if pb_10k:
        profile_fm["personal_bests"]["10k"] = {"time": pb_10k}
    if pb_hm:
        profile_fm["personal_bests"]["half_marathon"] = {"time": pb_hm}
    if pb_marathon:
        profile_fm["personal_bests"]["marathon"] = {"time": pb_marathon}
    if vo2max:
        profile_fm["personal_bests"]["vo2max_estimate"] = float(vo2max)

    profile_body = f"""# 竞技档案

## 基本信息
- 身高: {height} cm
- 体重: {weight} kg
- 年龄: {age}
- 性别: {gender}
- 地点: {location or '未设置'}

## 个人最佳
"""
    for dist, data in profile_fm["personal_bests"].items():
        if isinstance(data, dict) and "time" in data:
            profile_body += f"- **{dist}**: {data['time']}\n"

    profile_path = Path(config.memory_dir) / "profile" / "fitness-assessment.md"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(build_memory_file(profile_fm, profile_body), encoding="utf-8")
    console.print(f"  ✅ 竞技档案: {profile_path}")

    # Goal
    if has_goal.lower() == "y" and goal_name:
        goal_id = f"goal-{date.today().year}-{goal_distance}"
        goal_fm = {
            "type": "goal",
            "id": goal_id,
            "goal_type": "time_based",
            "category": "running",
            "status": "active",
            "priority": "high",
            "created": str(date.today()),
            "target_date": goal_date_str,
            "review_cycle": "weekly",
            "metrics": {
                f"target_{goal_distance}": goal_time,
                "weekly_mileage_km": int(goal_weekly_km) if goal_weekly_km.isdigit() else 50,
            },
            "tags": [goal_distance, str(date.today().year), "active"],
        }
        goal_body = f"""# {goal_name}

## 目标
- 距离: {goal_distance}
- 目标成绩: {goal_time}
- 截止日期: {goal_date_str}
- 周跑量目标: {goal_weekly_km} km

## 进度
创建于 {date.today()}，定期更新。
"""
        goal_path = Path(config.memory_dir) / "goals" / "active" / f"{goal_id}.md"
        goal_path.parent.mkdir(parents=True, exist_ok=True)
        goal_path.write_text(build_memory_file(goal_fm, goal_body), encoding="utf-8")
        console.print(f"  ✅ 训练目标: {goal_path}")

    # Preferences
    pref_fm = {
        "type": "coaching_preference",
        "updated": datetime.now().isoformat(timespec="seconds"),
        "training_preferences": {
            "preferred_workouts": [w.strip() for w in pref_workouts.split(",")],
            "preferred_time": pref_time,
            "preferred_terrain": [t.strip() for t in pref_terrain.split(",")],
        },
        "injury_history": [],
        "training_philosophy": training_philosophy,
        "tags": ["preferences", "coaching-style"],
    }
    if injury_history:
        pref_fm["injury_history"].append({
            "description": injury_history,
            "date": str(date.today()),
        })

    pref_body = f"""# 训练偏好

## 训练习惯
- 偏好时间: {pref_time}
- 偏好类型: {pref_workouts}
- 偏好地形: {pref_terrain}

## 训练哲学
{training_philosophy}

## 伤病史
{injury_history or '无'}
"""
    pref_path = Path(config.memory_dir) / "coaching" / "preferences.md"
    pref_path.parent.mkdir(parents=True, exist_ok=True)
    pref_path.write_text(build_memory_file(pref_fm, pref_body), encoding="utf-8")
    console.print(f"  ✅ 训练偏好: {pref_path}")

    console.print("\n[green]✅ Setup 完成！运行 rundown sync 同步数据，rundown daily 查看日报。[/]")


def _ask(prompt: str, default: str = "") -> str:
    """交互式提问，支持默认值。"""
    if default:
        result = input(f"  {prompt} [{default}]: ").strip()
        return result if result else default
    else:
        return input(f"  {prompt}: ").strip()


def cmd_mcp(args: argparse.Namespace) -> None:
    """mcp 命令：启动 MCP Server（stdio 模式，供 OpenClaw / Claude Desktop 调用）。"""
    config, auth, fetcher, storage, memory_store, user_id = _setup()

    from .mcp_server import create_server

    console.print("[bold blue]🔌 启动 Rundown MCP Server...[/]")

    server = create_server(config, auth, storage, memory_store, user_id)

    # FastMCP 通过 stdio 与客户端通信，不需要端口
    console.print("[green]✅ MCP Server 已启动 (stdio mode)[/]")
    console.print("[dim]等待 OpenClaw / Claude Desktop 连接...[/]")

    server.run(transport="stdio")


# ═══════════════════════════════════════════════════════════════
# Argument Parser
# ═══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="rundown",
        description="Rundown — Your daily running rundown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    sub = parser.add_subparsers(dest="command", help="可用命令")

    # ── init ──────────────────────────────────
    sub.add_parser("init", help="引导式创建配置文件 (.env)")

    # ── sync ──────────────────────────────────
    p_sync = sub.add_parser("sync", help="同步数据 + 生成记忆")
    p_sync.add_argument("--days", type=int, help="同步最近 N 天")
    p_sync.add_argument("--from", dest="from_date", help="起始日期 YYYY-MM-DD")
    p_sync.add_argument("--to", dest="to_date", help="结束日期 YYYY-MM-DD")
    p_sync.add_argument("--metrics", nargs="*", help="指定指标（逗号分隔）")
    p_sync.add_argument("--full", action="store_true", help="全量同步")
    p_sync.add_argument("--no-memory", action="store_true", help="仅同步数据，不生成记忆")

    # ── daily ─────────────────────────────────
    p_daily = sub.add_parser("daily", help="查看/生成每日综合报告")
    p_daily.add_argument("--date", help="报告日期 YYYY-MM-DD (默认今天)")
    p_daily.add_argument("--format", choices=["md", "json", "html"], default="md",
                         help="输出格式: md(终端) | json | html(静态网页)")
    p_daily.add_argument("--html", action="store_true",
                         help="输出为静态 HTML 文件 (output/YYYY-MM-DD.html)")
    p_daily.add_argument("--ai", action="store_true", help="调用 DeepSeek API 生成 AI 教练洞察")
    p_daily.add_argument("--image", action="store_true", help="导出为 PNG 图片（Playwright）")
    p_daily.add_argument("--theme", choices=["fresh", "sport", "dark"], default="sport", help="图片/HTML 主题 (默认 sport)")
    p_daily.add_argument("--no-ai", action="store_true", help="不使用任何 AI 洞察")

    # ── activities ────────────────────────────
    p_act = sub.add_parser("activities", help="查询活动列表")
    p_act.add_argument("--recent", type=int, help="最近 N 条")
    p_act.add_argument("--type", help="按运动类型筛选")
    p_act.add_argument("--export", help="导出 CSV 文件路径")

    # ── health ────────────────────────────────
    p_health = sub.add_parser("health", help="查询健康指标")
    p_health.add_argument("--metric", help="指定指标 key")
    p_health.add_argument("--days", type=int, help="最近 N 天")
    p_health.add_argument("--export", help="导出 CSV 文件路径")

    # ── memory ────────────────────────────────
    p_mem = sub.add_parser("memory", help="记忆管理")
    p_mem_sub = p_mem.add_subparsers(dest="memory_subcommand", help="子命令")

    p_list = p_mem_sub.add_parser("list", help="列出记忆")
    p_list.add_argument("--type", help="按类型筛选")
    p_list.add_argument("--status", help="按状态筛选")
    p_list.add_argument("--tag", help="按标签筛选")
    p_list.add_argument("--search", help="关键词搜索")

    p_show = p_mem_sub.add_parser("show", help="查看单条记忆")
    p_show.add_argument("memory_id", help="记忆 ID")

    p_sum = p_mem_sub.add_parser("summarize", help="手动触发摘要生成")
    p_sum.add_argument("--period", choices=["weekly", "monthly"], default="weekly")
    p_sum.add_argument("--date", help="目标日期")

    p_mem_sub.add_parser("check", help="完整性校验")
    p_mem_sub.add_parser("index", help="重建所有索引文件")

    # ── status ────────────────────────────────
    sub.add_parser("status", help="查看同步状态")

    # ── setup ─────────────────────────────────
    sub.add_parser("setup", help="交互式录入个人资料和目标")

    # ── mcp ───────────────────────────────────
    p_mcp = sub.add_parser("mcp", help="启动 MCP Server (stdio，供 OpenClaw 连接)")
    p_mcp.add_argument("--port", type=int, help="SSE 模式端口（默认 stdio）")

    return parser


# ═══════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════


COMMAND_HANDLERS = {
    "init": cmd_init,
    "sync": cmd_sync,
    "daily": cmd_daily,
    "activities": cmd_activities,
    "health": cmd_health,
    "memory": cmd_memory,
    "setup": cmd_setup,
    "status": cmd_status,
    "mcp": cmd_mcp,
}


def main(argv: list[str] | None = None) -> None:
    """Rundown CLI 入口。

    Args:
        argv: 命令行参数列表，None 表示使用 sys.argv。
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        console.print(f"[red]未知命令: {args.command}[/]")
        parser.print_help()
        return

    try:
        handler(args)
    except ConfigError as exc:
        console.print(f"[red]❌ 配置错误: {exc}[/]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]已取消[/]")
        sys.exit(0)
    except Exception as exc:
        logger.exception("命令执行失败")
        console.print(f"[red]❌ 错误: {exc}[/]")
        sys.exit(1)


if __name__ == "__main__":
    main()

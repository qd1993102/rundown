#!/usr/bin/env python3
"""按应用账号昵称导出脱敏单用户 tar。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console
from rich.table import Table

from src.user_data_archive import ArchiveError, export_user_data


class CliUsageError(ValueError):
    """可按选定输出格式渲染的参数错误。"""


class ExportArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = ExportArgumentParser(
        description="按应用账号昵称导出脱敏关联数据为未压缩 tar",
    )
    parser.add_argument("--nickname", "-n", required=True, help="应用账号昵称")
    parser.add_argument("--destination", "-d", required=True, help="已存在的目标目录")
    parser.add_argument("--email", "-e", help="重名时用于消歧的应用账号邮箱")
    parser.add_argument(
        "--output",
        "-o",
        choices=("table", "json"),
        default="table",
        help="结果输出格式（默认 table）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    output_format = _requested_output(raw_args)
    try:
        args = build_parser().parse_args(raw_args)
    except CliUsageError as exc:
        result = {
            "status": "failed",
            "error_code": "invalid_arguments",
            "retryable": False,
            "message": str(exc),
        }
        if output_format == "json":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            _print_table(result)
        return 2

    data_root = Path(
        os.getenv("NEURUN_DATA_DIR")
        or os.getenv("RUNDOWN_DATA_DIR")
        or ROOT / "data",
    )
    try:
        result = export_user_data(
            data_root=data_root,
            nickname=args.nickname,
            email=args.email,
            destination=args.destination,
        ).to_dict()
        exit_code = 0
    except ArchiveError as exc:
        result = exc.to_result()
        exit_code = exc.exit_code

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_table(result)
    return exit_code


def _requested_output(argv: Sequence[str]) -> str:
    for index, argument in enumerate(argv):
        if argument == "--output" or argument == "-o":
            if index + 1 < len(argv) and argv[index + 1] == "json":
                return "json"
        if argument == "--output=json" or argument == "-o=json":
            return "json"
    return "table"


def _print_table(result: dict[str, Any]) -> None:
    console = Console()
    table = Table(show_header=False, box=None)
    table.add_column("field", style="bold")
    table.add_column("value")
    for key, value in result.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value) or "-"
        else:
            rendered = str(value)
        table.add_row(key, rendered)
    console.print(table)


if __name__ == "__main__":
    raise SystemExit(main())

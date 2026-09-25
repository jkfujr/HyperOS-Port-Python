"""日志 i18n 自动改造工具。

扫描 src/ 下的 Python 源码，把 logger 调用改造成可翻译形式：

    logger.info(f"Detected Type: {rom_type}")     ->  logger.info(t("Detected Type: %s"), rom_type)
    logger.info("Cache cleared")                  ->  logger.info(t("Cache cleared"))
    logger.warning("Not found: %s", path)         ->  logger.warning(t("Not found: %s"), path)

要点：
- f-string 必须改写成延迟格式化（%s + 参数），否则 t() 收到的是已插值的结果，词条表无法匹配。
- 字面 % 在转成 % 风格后需要转义为 %%。
- 翻译函数刻意用 t() 而非 _()，因为项目内多处把 _ 当作循环忽略变量。
- 改造前会把原文件备份到 .i18n_backup/。

用法::

    python3 tools/i18n_wrap.py --dry-run          # 只报告，不落盘
    python3 tools/i18n_wrap.py --root src          # 实际改造
    python3 tools/i18n_wrap.py --check             # 校验词条表与源码是否一致
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

LEVELS = {"info", "warning", "error", "debug", "critical", "exception"}


class Replacement(NamedTuple):
    start: int  # 源码中的起始偏移
    end: int  # 源码中的结束偏移
    old: str  # 原文（用于校验）
    new: str  # 替换文本
    kind: str  # fstring / plain / skipped


def owner_name(node: ast.Call) -> Optional[str]:
    """返回日志器对象的名字，如 logger / self.logger 里的 'logger'。"""
    val = node.func.value
    if isinstance(val, ast.Name):
        return val.id
    if isinstance(val, ast.Attribute):
        return val.attr
    return None


def is_logger_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in LEVELS:
        return False
    owner = owner_name(node)
    return owner is not None and owner.lower().endswith(("logger", "log"))


def unquote(src: str) -> str:
    """去掉 ast.unparse 产生的引号。"""
    if len(src) >= 2 and src[0] == src[-1] and src[0] in "'\"":
        return src[1:-1]
    return src


def format_spec_text(node: ast.AST) -> str:
    """提取格式说明符的纯文本（如 .2f）。

    不能对 format_spec 直接用 ast.unparse：它会被还原成 f'.2f' 这样的源码，
    脱引号后残留 f 前缀，生成出 %sf'.2f' 这种错误占位符。
    """
    parts: List[str] = []
    for v in getattr(node, "values", []):
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            # 动态格式说明符（嵌套替换），交人工处理
            raise ValueError("动态格式说明符")
    return "".join(parts)


def build_placeholder(value: ast.FormattedValue) -> str:
    """把 f-string 的一个占位符转换成 % 风格。"""
    # conversion: -1 无, 115 's', 114 'r', 97 'a'
    conv = {-1: "s", 115: "s", 114: "r", 97: "a"}.get(value.conversion, "s")
    if value.format_spec is not None:
        spec = format_spec_text(value.format_spec)
        # 格式说明符自带类型字符（如 .2f / d），不能再叠加转换符，
        # 否则 {x:.2f} 会错误地变成 %s.2f（正确应为 %.2f）
        if spec:
            return f"%{spec}"
    return f"%{conv}"


def convert_fstring(node: ast.JoinedStr) -> Tuple[str, List[str]]:
    """把 f-string 拆成 (% 风格格式串, 表达式源码列表)。"""
    parts: List[str] = []
    exprs: List[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            # 转为 % 风格后，字面 % 必须转义
            parts.append(value.value.replace("%", "%%"))
        elif isinstance(value, ast.FormattedValue):
            parts.append(build_placeholder(value))
            exprs.append(ast.unparse(value.value))
        else:
            # 非常规结构（嵌套 f-string 等），交人工处理
            raise ValueError("unsupported JoinedStr part")
    return "".join(parts), exprs


def build_new_call(node: ast.Call) -> Optional[Tuple[str, str]]:
    """生成改造后的调用源码；返回 (新文本, 类别)，无法处理时返回 None。"""
    if not node.args:
        return None
    first = node.args[0]
    func_src = ast.unparse(node.func)

    if isinstance(first, ast.JoinedStr):
        try:
            fmt, exprs = convert_fstring(first)
        except ValueError:
            return None
        new_args = [f"t({fmt!r})", *exprs]
        kind = "fstring"
    elif isinstance(first, ast.Constant) and isinstance(first.value, str):
        fmt = first.value
        new_args = [f"t({fmt!r})"]
        kind = "plain"
    else:
        return None

    # 保留原有的其余位置参数与关键字参数
    for extra in node.args[1:]:
        new_args.append(ast.unparse(extra))
    for kw in node.keywords:
        if kw.arg is None:
            new_args.append(f"**{ast.unparse(kw.value)}")
        else:
            new_args.append(f"{kw.arg}={ast.unparse(kw.value)}")

    return f"{func_src}({', '.join(new_args)})", kind


def offset_of(source: str, node: ast.AST) -> Optional[int]:
    """按 lineno/col_offset 计算节点在源码中的字符偏移。"""
    lines = source.splitlines(keepends=True)
    if node.lineno > len(lines):
        return None
    start = sum(len(lines[i]) for i in range(node.lineno - 1))
    # col_offset 是 UTF-8 字节偏移，含非 ASCII 时需换算
    line = lines[node.lineno - 1]
    prefix = line[: node.col_offset]
    start += len(prefix)
    return start


def process_file(path: Path) -> Tuple[List[Replacement], Optional[str]]:
    """扫描单个文件，返回（替换列表, 错误）。"""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], f"语法错误，跳过: {exc}"

    replacements: List[Replacement] = []
    skipped = 0

    for node in ast.walk(tree):
        if not is_logger_call(node):
            continue
        result = build_new_call(node)
        if result is None:
            skipped += 1
            continue
        new, kind = result
        old = ast.get_source_segment(source, node)
        if old is None:
            skipped += 1
            continue
        start = offset_of(source, node)
        if start is None or source[start : start + len(old)] != old:
            # 偏移换算不可靠（多字节字符等），放弃该处，避免改坏
            skipped += 1
            continue
        replacements.append(
            Replacement(start=start, end=start + len(old), old=old, new=new, kind=kind)
        )

    # 从后往前替换，避免前面的偏移失效
    out = source
    for rep in sorted(replacements, key=lambda r: r.start, reverse=True):
        out = out[: rep.start] + rep.new + out[rep.end :]

    if replacements:
        out = insert_import(out)

    return replacements, (f"{skipped} 处需人工处理" if skipped else None)


def insert_import(source: str) -> str:
    """在最后一个顶层 import 之后插入翻译函数的导入。"""
    if "from src.utils.i18n import t" in source:
        return source
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    last_import_end = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            line_end = getattr(node, "end_lineno", None)
            if line_end:
                last_import_end = line_end
    if last_import_end is None:
        return f"from src.utils.i18n import t\n{source}"

    lines = source.splitlines(keepends=True)
    insert_at = last_import_end  # 插在第 last_import_end 行之后
    head = "".join(lines[:insert_at])
    tail = "".join(lines[insert_at:])
    return f"{head}from src.utils.i18n import t\n{tail}"


PLACEHOLDER = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa%]")


def is_translatable(fmt: str) -> bool:
    """判断格式串是否含可翻译文本。

    纯占位符串（如 '%s%s%s'，仅用于拼接颜色码）去掉占位符后为空，没有可翻译
    内容，不应计入覆盖率，否则校验器会长期报假红。
    """
    return PLACEHOLDER.sub("", fmt).strip() != ""


def literal_key(node: ast.Call) -> Tuple[Optional[str], str]:
    """取日志调用的词条键；状态为 ok（已改造）/ unwrapped（未改造）/ dynamic。"""
    if not node.args:
        return None, "dynamic"
    first = node.args[0]
    if (
        isinstance(first, ast.Call)
        and isinstance(first.func, ast.Name)
        and first.func.id == "t"
        and len(first.args) == 1
        and isinstance(first.args[0], ast.Constant)
        and isinstance(first.args[0].value, str)
    ):
        return first.args[0].value, "ok"
    if isinstance(first, ast.JoinedStr):
        return None, "unwrapped"
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return None, "unwrapped" if is_translatable(first.value) else "dynamic"
    return None, "dynamic"


def collect_keys(root: Path) -> Tuple[Dict[str, List[str]], List[str]]:
    """扫描源码，返回（词条 -> 出现位置, 未改造调用位置）。"""
    keys: Dict[str, List[str]] = {}
    unwrapped: List[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "i18n.py":  # 翻译模块自身不需要改造
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            unwrapped.append(f"{path}: 语法错误 {exc}")
            continue
        for node in ast.walk(tree):
            if not is_logger_call(node):
                continue
            key, state = literal_key(node)
            where = f"{path}:{node.lineno}"
            if state == "ok":
                keys.setdefault(key, []).append(where)
            elif state == "unwrapped":
                unwrapped.append(where)
    return keys, unwrapped


def run_check(root: Path) -> int:
    """校验词条表与源码的一致性，返回进程退出码。"""
    keys, unwrapped = collect_keys(root)
    zh_path = root / "utils" / "locales" / "zh.json"
    table = json.loads(zh_path.read_text(encoding="utf-8")) if zh_path.exists() else {}
    missing = sorted(k for k in keys if k not in table)
    unused = sorted(k for k in table if k not in keys)

    print(f"已改造调用     : {sum(len(v) for v in keys.values())} 处")
    print(f"唯一词条       : {len(keys)}")
    print(f"词条表条目     : {len(table)}")
    print(f"未改造调用     : {len(unwrapped)}")
    print(f"缺失翻译       : {len(missing)}")
    print(f"表中未使用     : {len(unused)}")
    for k in missing[:20]:
        print(f"  [缺失]   {keys[k][0]}: {k!r}")
    for k in unused[:20]:
        print(f"  [未使用] {k!r}")
    for w in unwrapped[:20]:
        print(f"  [未改造] {w}")
    return 1 if missing or unwrapped else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="日志 i18n 自动改造工具")
    parser.add_argument("--root", default="src", help="源码根目录")
    parser.add_argument("--dry-run", action="store_true", help="只报告不落盘")
    parser.add_argument("--check", action="store_true", help="校验词条表与源码的一致性")
    parser.add_argument(
        "--backup-dir", default=".i18n_backup", help="备份目录（相对 root 的父目录）"
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"目录不存在: {root}")
        return 1

    if args.check:
        return run_check(root)

    files = sorted(root.rglob("*.py"))
    totals: Dict[str, int] = {"fstring": 0, "plain": 0}
    touched = 0
    notes: List[str] = []

    backup_root = root.parent / args.backup_dir

    for path in files:
        replacements, note = process_file(path)
        if not replacements:
            if note:
                notes.append(f"{path}: {note}")
            continue

        touched += 1
        for rep in replacements:
            totals[rep.kind] = totals.get(rep.kind, 0) + 1
        if note:
            notes.append(f"{path}: {note}")

        if not args.dry_run:
            if not path.name == "i18n.py":  # i18n 模块自身不需要改造
                rel = path.relative_to(root.parent)
                backup = backup_root / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)

                # 保持文件原有的换行风格。write_text 默认按平台转换换行符，
                # 会把 LF 写成 CRLF（Windows）、把 CRLF 写成 LF（Linux），
                # 导致整个代码库的行尾被无声改写。
                newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
                source = path.read_text(encoding="utf-8")
                out = source
                for rep in sorted(replacements, key=lambda r: r.start, reverse=True):
                    out = out[: rep.start] + rep.new + out[rep.end :]
                out = insert_import(out)
                path.write_text(out, encoding="utf-8", newline=newline)

    print(f"扫描文件数        : {len(files)}")
    print(f"被改造的文件数    : {touched}")
    print(f"改造 f-string     : {totals.get('fstring', 0)}")
    print(f"改造纯字符串      : {totals.get('plain', 0)}")
    print(f"合计              : {totals.get('fstring', 0) + totals.get('plain', 0)}")
    if notes:
        print("\n需人工处理的地方:")
        for n in notes[:40]:
            print(f"  {n}")
    print(f"\n模式: {'预览（未落盘）' if args.dry_run else '已写入'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

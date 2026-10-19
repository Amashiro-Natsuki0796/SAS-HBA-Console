"""把含中文的界面文案包进 t()，使用 tokenize 精确定位。

设计要点
--------
* ``tokenize`` 的列号是**字符**列，在多字节行上依然准确，
  不像 Python 3.13 的 AST ``col_offset`` 在同一行多个字面量时会错位。
* 只处理真正的字符串字面量 token，不会误伤 ``set_text("`` 这类包含
  ``t("`` 子串的方法名。
* 自动识别并跳过：docstring、f-string 的 ``.format`` 占位符已正确转换、
  CSS/内部标识、比较运算中的字符串。
* 处理后立即 ``ast.parse`` 校验，失败则不落盘。

用法::

    python3 scripts/wrap_i18n.py --check          # 只统计
    python3 scripts/wrap_i18n.py --apply f1 f2    # 实际改写
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import tokenize

CJK = re.compile(r"[\u4e00-\u9fff]")

# 这些上下文里的字符串是内部标识，不能翻译
SKIP_PREV_NAMES = {"set_name", "set_widget_name"}
SKIP_STRINGS = {"text", "app-bg", "card", "pill", "muted", "tiny", "bold", "mono"}


def has_cjk(text: str) -> bool:
    return bool(CJK.search(text))


def string_tokens(src: str):
    """产出 (start, end, token)。start/end 为 (row, col)，字符列。"""
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            yield tok


def docstring_ranges(tree: ast.AST) -> set[tuple[int, int]]:
    """收集所有 docstring 字面量的 (row, col)。"""
    marks: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    marks.add((value.lineno, value.col_offset))
    return marks


def t_call_ranges(src: str) -> list[tuple[int, int, int, int]]:
    """收集已经包在 t(...) 中的字符串 token 位置，便于跳过。"""
    ranges = []
    toks = list(string_tokens(src))
    tree = ast.parse(src)
    # 用 AST 找出 t( 调用，再用 token 位置判断
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "t":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    ranges.append((arg.lineno, arg.col_offset))
    return ranges


def compare_ranges(tree: ast.AST) -> set[tuple[int, int]]:
    """比较运算里出现的字符串（内部标识）。"""
    marks: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for part in [node.left, *node.comparators]:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    marks.add((part.lineno, part.col_offset))
                elif isinstance(part, (ast.Tuple, ast.List, ast.Set)):
                    for item in part.elts:
                        if isinstance(item, ast.Constant) and isinstance(
                            item.value, str
                        ):
                            marks.add((item.lineno, item.col_offset))
    return marks


def convert_fstring(body: str) -> str:
    """把 f-string 的 body 转成 {n} 占位符形式，返回 t() 的模板串。"""
    # body 不含首尾引号；把 {expr} 替换为 {n}，收集 expr
    out = []
    args = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "{":
            if i + 1 < n and body[i + 1] == "{":
                out.append("{")
                i += 2
                continue
            depth = 1
            j = i + 1
            while j < n and depth:
                if body[j] == "{":
                    depth += 1
                elif body[j] == "}":
                    depth -= 1
                j += 1
            expr = body[i + 1 : j - 1]
            # 处理 format spec: {x:.1f}
            spec = ""
            if ":" in expr and not expr.startswith(("'", '"')):
                head, _, tail = expr.partition(":")
                if head.strip().isidentifier() or "[" in head or "." in head:
                    expr, spec = head, tail
            if spec:
                expr = f'format({expr}, "{spec}")'
            out.append("{%d}" % len(args))
            args.append(expr)
            i = j
        elif ch == "}":
            if i + 1 < n and body[i + 1] == "}":
                out.append("}")
                i += 2
                continue
            out.append("}")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), args



def merge_adjacent_literals(src: str) -> str:
    """用 tokenize 找相邻的字符串字面量并合并成单个 f-string。

    相邻判定：两个 STRING token 之间只有 NL / NEWLINE / 缩进 / 续行符号。
    合并时保留前缀与行尾逗号。
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return src

    lines = src.split("\n")
    # 收集所有 STRING token 及其前一个有效 token
    strings = []
    for idx, tok in enumerate(toks):
        if tok.type != tokenize.STRING:
            continue
        prev = None
        for back in range(idx - 1, -1, -1):
            t = toks[back]
            if t.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
                          tokenize.DEDENT, tokenize.COMMENT):
                continue
            prev = t
            break
        strings.append((idx, tok, prev))

    # 分组：相邻且 prev 是 STRING 或开头
    groups = []
    current = []
    for idx, tok, prev in strings:
        if prev is not None and prev.type == tokenize.STRING and current:
            current.append((idx, tok))
        else:
            if len(current) > 1:
                groups.append(current)
            current = [(idx, tok)]
    if len(current) > 1:
        groups.append(current)

    for group in groups:
        first_tok = group[0][1]
        last_tok = group[-1][1]
        row = first_tok.start[0]
        line = lines[row - 1]
        start_col = first_tok.start[1]
        end_col = last_tok.end[1]
        prefix = first_tok.string[: len(first_tok.string) - len(first_tok.string.lstrip("fFrRbBuU"))]
        q = first_tok.string.lstrip("fFrRbBuU")[0]
        qlen = 3 if first_tok.string.lstrip("fFrRbBuU")[:3] == q * 3 else 1
        body = "".join(
            t.string.lstrip("fFrRbBuU")[qlen:-qlen] for _, t in group
        )
        end_row, end_col = last_tok.end
        # 末尾残余（逗号、右括号等）来自**最后一段**所在行
        tail = lines[end_row - 1][end_col:]
        if end_row == row:
            lines[row - 1] = line[:start_col] + prefix + q + body + q + tail
        else:
            lines[row - 1] = line[:start_col] + prefix + q + body + q + tail
            for extra in range(row, end_row):
                lines[extra] = ""
    return "\n".join(lines)


def process(path: str, apply: bool) -> tuple[int, int]:
    src = open(path, encoding="utf-8").read()
    merged = merge_adjacent_literals(src)
    if merged != src:
        try:
            ast.parse(merged)
            src = merged
        except SyntaxError:
            pass  # 合并失败则保持原样
    tree = ast.parse(src)
    doc_marks = docstring_ranges(tree)
    t_marks = set(t_call_ranges(src))
    cmp_marks = compare_ranges(tree)
    skip = doc_marks | t_marks | cmp_marks

    lines = src.split("\n")
    # 先按行分组收集 token
    by_line: dict[int, list] = {}
    for tok in string_tokens(src):
        if not has_cjk(tok.string):
            continue
        row, col = tok.start
        if (row, col) in skip:
            continue
        by_line.setdefault(row, []).append(tok)

    if not by_line:
        return 0, 0

    changed = 0
    for row, toks in sorted(by_line.items()):
        line = lines[row - 1]
        # 从右往左替换，保证左侧列号仍然有效
        for tok in sorted(toks, key=lambda t: t.start[1], reverse=True):
            start, end = tok.start[1], tok.end[1]
            literal = line[start:end]
            prefix = literal[: len(literal) - len(literal.lstrip("fFrRbBuU"))]
            is_f = "f" in prefix.lower()
            quote_len = 3 if literal.lstrip("fFrRbBuU")[:3] in ('"""', "'''") else 1
            q = literal.lstrip("fFrRbBuU")[0]
            body = literal[len(prefix) + quote_len : len(literal) - quote_len]
            if is_f:
                template, args = convert_fstring(body)
                tmpl = template.replace("\\", "\\\\").replace('"', '\\"')
                new = 't("' + tmpl + '"' + "".join(", " + a for a in args) + ")"
            else:
                new = f"t({literal})"
            line = line[:start] + new + line[end:]
            changed += 1
        lines[row - 1] = line

    out = "\n".join(lines)
    try:
        ast.parse(out)
    except SyntaxError as exc:
        print(f"  SKIP {path}: 改写后语法错误 line {exc.lineno}，未写入")
        return 0, len(skip)

    if apply:
        open(path, "w", encoding="utf-8").write(out)
    return changed, 0


def main() -> int:
    parser = argparse.ArgumentParser(description="把中文界面文案包进 t()")
    parser.add_argument("files", nargs="*")
    parser.add_argument("--apply", action="store_true", help="实际写入")
    parser.add_argument("--check", action="store_true", help="只统计")
    args = parser.parse_args()

    files = args.files or []
    if not files:
        print("请提供要处理的 Python 文件")
        return 1

    total = 0
    for path in files:
        n, _ = process(path, apply=args.apply and not args.check)
        total += n
        print(f"{path}: {n} 处以 t() 包裹")
    print(f"合计 {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

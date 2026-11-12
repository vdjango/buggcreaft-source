"""Generate a complete Python class/function index and check it in CI."""

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "文档" / "18-已有类与方法索引.md"


def render() -> str:
    rows = [
        "# 已有类与方法索引",
        "",
        "> 自动生成，勿手改。运行 python tools/update_method_index.py；CI 使用 --check。",
        "",
        "仅记录实际源码。模块契约见 [共享接口与能力目录](12-共享接口与能力目录.md)。",
        "前端组件/函数签名见 [前端函数与组件索引](28-前端函数与组件索引.md)，行为见第 08 号。",
        "",
        "| 文件 | 类/函数/方法 | 签名 |",
        "| --- | --- | --- |",
    ]
    for directory in ("src", "tests", "tools", "migrations"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            relative = path.relative_to(ROOT).as_posix()
            collect_symbols(tree, [], relative, rows)
    return "\n".join(rows) + "\n"


def collect_symbols(node: ast.AST, scope: list[str], relative: str, rows: list[str]) -> None:
    for child in ast.iter_child_nodes(node):
        nested = scope
        if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            nested = [*scope, child.name]
            signature = "class" if isinstance(child, ast.ClassDef) else ast.unparse(child.args)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.returns:
                signature += " -> " + ast.unparse(child.returns)
            signature = signature.replace("|", "&#124;").replace("\n", " ")
            rows.append(
                f"| [{relative}]({('../' + relative)}) | `{'.'.join(nested)}` | `{signature}` |"
            )
        collect_symbols(child, nested, relative, rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != result:
            raise SystemExit("方法索引过期：运行 python tools/update_method_index.py")
    else:
        TARGET.write_text(result, encoding="utf-8")


if __name__ == "__main__":
    main()

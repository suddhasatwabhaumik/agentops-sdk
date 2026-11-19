# Copyright 2026 Google. This software is provided as-is, without
# warranty or representation for any use or purpose. Your use of it is
# subject to your agreement with Google.
"""Strict AST allowlist validator for custom evaluation metric functions."""

from __future__ import annotations

import ast

_MAX_METRIC_CODE_CHARS = 16_384
_ALLOWED_IMPORTS = frozenset(
    {
        "math",
        "re",
        "json",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "string",
        "typing",
        "decimal",
        "fractions",
        "difflib",
    }
)
_FORBIDDEN_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "breakpoint",
        "input",
        "globals",
        "locals",
        "vars",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "__builtins__",
        "memoryview",
        "object",
        "type",
        "exit",
        "quit",
        "help",
        "copyright",
        "credits",
        "license",
    }
)
_FORBIDDEN_ATTRS = frozenset(
    {
        "system",
        "popen",
        "spawn",
        "execv",
        "execl",
        "eval",
        "exec",
        "compile",
        "breakpoint",
    }
)
_SAFE_CALLABLE_BUILTINS = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "frozenset",
        "int",
        "isinstance",
        "len",
        "list",
        "map",
        "max",
        "min",
        "pow",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }
)
_ALLOWED_AST_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        ast.Module,
        ast.FunctionDef,
        ast.arguments,
        ast.arg,
        ast.Return,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Pass,
        ast.Expr,
        ast.If,
        ast.For,
        ast.While,
        ast.Break,
        ast.Continue,
        ast.Try,
        ast.ExceptHandler,
        ast.Raise,
        ast.Import,
        ast.ImportFrom,
        ast.alias,
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Constant,
        ast.JoinedStr,
        ast.FormattedValue,
        ast.List,
        ast.Tuple,
        ast.Set,
        ast.Dict,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        ast.Subscript,
        ast.Slice,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Lambda,
        ast.Call,
        ast.Attribute,
        ast.keyword,
        ast.Starred,
        ast.NamedExpr,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.LShift,
        ast.RShift,
        ast.BitOr,
        ast.BitXor,
        ast.BitAnd,
        ast.Invert,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
    }
)


def _validate_import(
    node: ast.Import | ast.ImportFrom, imported: set[str]
) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if alias.name == "*" or top not in _ALLOWED_IMPORTS:
                raise ValueError(f"Forbidden import: {alias.name!r}")
        return
    if node.level != 0 or not node.module:
        raise ValueError("Relative or empty imports are forbidden.")
    top = node.module.split(".")[0]
    if top not in _ALLOWED_IMPORTS:
        raise ValueError(f"Forbidden import from {node.module!r}")
    for alias in node.names:
        if alias.name == "*" or alias.name.startswith("_"):
            raise ValueError(
                f"Forbidden wildcard/private import: {alias.name!r}"
            )
        imported.add(alias.asname or alias.name)


def _validate_call(node: ast.Call, allowed_bare_calls: frozenset[str]) -> None:
    if isinstance(node.func, ast.Name):
        if node.func.id not in allowed_bare_calls:
            raise ValueError(f"Disallowed function call: {node.func.id!r}")
        return
    if not isinstance(node.func, ast.Attribute):
        raise ValueError("Indirect or dynamic calls are forbidden.")


def _validate_ast_tree(tree: ast.Module) -> None:
    defined_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    imported_callables: set[str] = set()
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_AST_NODES:
            raise ValueError(f"Disallowed Python syntax: {type(node).__name__}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _validate_import(node, imported_callables)
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRS:
                raise ValueError(f"Forbidden attribute access: {node.attr!r}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_BUILTINS:
            raise ValueError(f"Forbidden builtin reference: {node.id!r}")
    allowed_bare = _SAFE_CALLABLE_BUILTINS | defined_fns | imported_callables
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            _validate_call(node, allowed_bare)


def validate_metric_code(code: str, required_fn: str | None = None) -> str:
    """Validates metric Python code against a strict AST allowlist (CWE-94).

    Args:
        code: Source string defining a custom Python evaluation function.
        required_fn: Optional top-level function name that must be defined.

    Returns:
        The validated Python source string unchanged.

    Raises:
        ValueError: If code exceeds bounds, fails syntax, or uses bad AST.
    """
    if (
        not isinstance(code, str)
        or not code.strip()
        or len(code) > _MAX_METRIC_CODE_CHARS
    ):
        raise ValueError(
            f"Code must be non-empty and <= {_MAX_METRIC_CODE_CHARS} chars."
        )
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax: {exc}") from exc
    _validate_ast_tree(tree)
    if required_fn and not any(
        isinstance(node, ast.FunctionDef) and node.name == required_fn
        for node in tree.body
    ):
        raise ValueError(f"Code must define {required_fn!r}.")
    return code

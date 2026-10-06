"""Extract the public API surface of a Python codebase with `ast`.

The surface is: public functions/classes/methods (with signatures, defaults,
decorators, docstrings), argparse/click CLI options, environment variables,
config keys and module paths. Nothing is imported or executed.
"""
from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "env", "__pycache__", "node_modules", "build", "dist",
    "tests", "test", "docs", ".tox", ".pytest_cache", "site-packages",
}
CONFIG_NAMES = {"config", "settings", "cfg", "conf"}


@dataclass(frozen=True)
class Param:
    name: str
    kind: str = "POSITIONAL_OR_KEYWORD"  # inspect.Parameter kind name
    default: str | None = None  # unparsed source of the default, None if required
    annotation: str | None = None

    @property
    def required(self) -> bool:
        return self.default is None and self.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")


@dataclass
class Symbol:
    qualname: str
    module: str
    name: str
    kind: str  # function | class | method | property
    params: tuple[Param, ...] = ()
    decorators: tuple[str, ...] = ()
    doc: str = ""
    file: str = ""
    line: int = 0
    owner: str | None = None  # qualname of the owning class for methods

    def signature(self) -> inspect.Signature:
        ps = []
        for p in self.params:
            default = inspect.Parameter.empty if p.required or p.default is None else None
            ps.append(inspect.Parameter(p.name, getattr(inspect.Parameter, p.kind), default=default))
        return inspect.Signature(ps)

    def signature_text(self) -> str:
        parts = []
        for p in self.params:
            prefix = {"VAR_POSITIONAL": "*", "VAR_KEYWORD": "**"}.get(p.kind, "")
            parts.append(f"{prefix}{p.name}" + (f"={p.default}" if p.default is not None else ""))
        return f"{self.name}({', '.join(parts)})"


@dataclass
class CliOption:
    module: str
    flags: tuple[str, ...]
    default: str | None = None
    required: bool = False
    takes_value: bool = True
    help: str = ""
    file: str = ""
    line: int = 0

    @property
    def primary(self) -> str:
        longs = [f for f in self.flags if f.startswith("--")]
        return longs[0] if longs else self.flags[0]


@dataclass
class EnvVar:
    name: str
    default: str | None
    module: str
    file: str = ""
    line: int = 0


@dataclass
class ConfigKey:
    name: str
    default: str | None
    module: str


@dataclass
class ApiSurface:
    root: str = ""
    symbols: dict[str, Symbol] = field(default_factory=dict)
    cli: dict[str, CliOption] = field(default_factory=dict)
    env: dict[str, EnvVar] = field(default_factory=dict)
    config: dict[str, ConfigKey] = field(default_factory=dict)
    modules: dict[str, frozenset[str]] = field(default_factory=dict)  # module -> public names
    errors: list[str] = field(default_factory=list)

    def packages(self) -> set[str]:
        return {m.split(".")[0] for m in self.modules}

    def lookup(self, dotted: str) -> Symbol | None:
        """Find a symbol by exact or unambiguous suffix match.

        Bare names only match functions and classes (a bare `get` never matches
        a method); methods need at least `Class.method`.
        """
        if dotted in self.symbols:
            return self.symbols[dotted]
        hits = [s for q, s in self.symbols.items() if q.endswith("." + dotted)]
        if "." not in dotted:
            hits = [s for s in hits if s.kind in ("function", "class")]
        return hits[0] if len(hits) == 1 else None


def module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _params(args: ast.arguments, drop_first: bool) -> tuple[Param, ...]:
    out: list[Param] = []
    positional = [(a, "POSITIONAL_ONLY") for a in args.posonlyargs] + [
        (a, "POSITIONAL_OR_KEYWORD") for a in args.args
    ]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for (a, kind), d in zip(positional, defaults):
        out.append(Param(a.arg, kind, ast.unparse(d) if d is not None else None,
                         ast.unparse(a.annotation) if a.annotation else None))
    if args.vararg:
        out.append(Param(args.vararg.arg, "VAR_POSITIONAL"))
    for a, d in zip(args.kwonlyargs, args.kw_defaults):
        out.append(Param(a.arg, "KEYWORD_ONLY", ast.unparse(d) if d is not None else None,
                         ast.unparse(a.annotation) if a.annotation else None))
    if args.kwarg:
        out.append(Param(args.kwarg.arg, "VAR_KEYWORD"))
    return tuple(out[1:] if drop_first and out else out)


def _decorators(node) -> tuple[str, ...]:
    return tuple(ast.unparse(d) for d in node.decorator_list)


def _class_params(cls: ast.ClassDef) -> tuple[Param, ...]:
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
            return _params(item.args, drop_first=True)
    if any("dataclass" in d for d in _decorators(cls)):
        fields = []
        for item in cls.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                default = None
                if item.value is not None:
                    default = ast.unparse(item.value)
                    if isinstance(item.value, ast.Call) and ast.unparse(item.value.func).endswith("field"):
                        kws = {k.arg: ast.unparse(k.value) for k in item.value.keywords}
                        default = kws.get("default", "<factory>" if "default_factory" in kws else None)
                fields.append(Param(item.target.id, "POSITIONAL_OR_KEYWORD", default, ast.unparse(item.annotation)))
        return tuple(fields)
    return ()


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os") or (
        isinstance(node, ast.Name) and node.id == "environ")


def _const_str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _env_use(node: ast.AST) -> tuple[str, str | None] | None:
    if isinstance(node, ast.Call):
        f = node.func
        is_get = (isinstance(f, ast.Attribute) and f.attr == "get" and _is_environ(f.value)) or (
            isinstance(f, ast.Attribute) and f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os"
        ) or (isinstance(f, ast.Name) and f.id == "getenv")
        if is_get and node.args and _const_str(node.args[0]):
            default = None
            if len(node.args) > 1:
                default = ast.unparse(node.args[1])
            for k in node.keywords:
                if k.arg == "default":
                    default = ast.unparse(k.value)
            return _const_str(node.args[0]), default
    if isinstance(node, ast.Subscript) and _is_environ(node.value) and _const_str(node.slice):
        return _const_str(node.slice), None
    return None


def _is_config(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id in CONFIG_NAMES) or (
        isinstance(node, ast.Attribute) and node.attr in CONFIG_NAMES)


def _config_use(node: ast.AST) -> tuple[str, str | None] | None:
    if isinstance(node, ast.Subscript) and _is_config(node.value) and _const_str(node.slice):
        return _const_str(node.slice), None
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and _is_config(node.func.value) and node.args and _const_str(node.args[0])):
        return _const_str(node.args[0]), ast.unparse(node.args[1]) if len(node.args) > 1 else None
    return None


def _cli_option(call: ast.Call, module: str, file: str, click: bool) -> CliOption | None:
    flags = tuple(a.value for a in call.args
                  if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("-"))
    if not flags:
        return None
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    action = _const_str(kw.get("action")) or ""
    is_flag = action in ("store_true", "store_false", "count") or (
        click and isinstance(kw.get("is_flag"), ast.Constant) and kw["is_flag"].value is True)
    default = ast.unparse(kw["default"]) if "default" in kw else ("False" if is_flag else None)
    required = isinstance(kw.get("required"), ast.Constant) and kw["required"].value is True
    return CliOption(module, flags, default, required, not is_flag, _const_str(kw.get("help")) or "",
                     file, call.lineno)


def _walk_module(tree: ast.Module, module: str, file: str, api: ApiSurface) -> None:
    public: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            q = f"{module}.{node.name}" if module else node.name
            api.symbols[q] = Symbol(q, module, node.name, "function", _params(node.args, False),
                                    _decorators(node), ast.get_docstring(node) or "", file, node.lineno)
            public.add(node.name)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            q = f"{module}.{node.name}" if module else node.name
            api.symbols[q] = Symbol(q, module, node.name, "class", _class_params(node),
                                    _decorators(node), ast.get_docstring(node) or "", file, node.lineno)
            public.add(node.name)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    decos = _decorators(item)
                    kind = "property" if "property" in decos else "method"
                    drop = "staticmethod" not in decos
                    mq = f"{q}.{item.name}"
                    api.symbols[mq] = Symbol(mq, module, item.name, kind,
                                             () if kind == "property" else _params(item.args, drop),
                                             decos, ast.get_docstring(item) or "", file, item.lineno, owner=q)
    api.modules[module] = frozenset(public)

    for node in ast.walk(tree):
        env = _env_use(node)
        if env:
            api.env.setdefault(env[0], EnvVar(env[0], env[1], module, file, getattr(node, "lineno", 0)))
        cfg = _config_use(node)
        if cfg:
            api.config.setdefault(cfg[0], ConfigKey(cfg[0], cfg[1], module))
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "add_argument":
                opt = _cli_option(node, module, file, click=False)
                if opt:
                    api.cli[opt.primary] = opt
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if isinstance(d, ast.Call):
                    name = d.func.attr if isinstance(d.func, ast.Attribute) else getattr(d.func, "id", "")
                    if name == "option":
                        opt = _cli_option(d, module, file, click=True)
                        if opt:
                            api.cli[opt.primary] = opt


def extract_api(root: str | Path, src: str = ".") -> ApiSurface:
    """Parse every non-test .py file under root/src into an ApiSurface."""
    base = Path(root) / src
    api = ApiSurface(root=str(base))
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base)
        if any(part in EXCLUDED_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            api.errors.append(f"{rel.as_posix()}: {exc}")
            continue
        _walk_module(tree, module_name(rel), rel.as_posix(), api)
    return api

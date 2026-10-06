"""Structured diff between two API surfaces."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .code_analysis import ApiSurface, CliOption, Param, Symbol

RENAME_THRESHOLD = 0.65


@dataclass
class CodeChange:
    kind: str
    subject: str  # base qualname, flag, env var, config key or module
    old: str = ""
    new: str = ""
    similarity: float = 1.0  # confidence that this is what happened (renames)
    param: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    base_symbol: Symbol | None = None
    head_symbol: Symbol | None = None
    base_cli: CliOption | None = None
    hint: str = ""  # text used for semantic matching

    def describe(self) -> str:
        k = self.kind
        if k == "symbol_removed":
            return f"`{self.subject}` was removed"
        if k == "symbol_renamed":
            return f"`{self.old}` was renamed to `{self.new}` (similarity {self.similarity:.2f})"
        if k == "symbol_moved":
            return f"`{self.old}` moved to `{self.new}`"
        if k == "module_moved":
            return f"module `{self.old}` moved to `{self.new}`"
        if k == "default_changed":
            return f"default of `{self.param}` in `{self.subject}` changed from `{self.old_value}` to `{self.new_value}`"
        if k == "param_renamed":
            return f"parameter `{self.old}` of `{self.subject}` was renamed to `{self.new}`"
        if k == "param_removed":
            return f"parameter `{self.param}` was removed from `{self.subject}`"
        if k == "param_added_required":
            return f"`{self.subject}` has a new required parameter `{self.param}`"
        if k == "param_added_optional":
            return f"`{self.subject}` has a new optional parameter `{self.param}`"
        if k == "param_reordered":
            return f"positional parameters of `{self.subject}` were reordered ({self.old} -> {self.new})"
        if k == "cli_flag_removed":
            return f"CLI flag `{self.subject}` was removed"
        if k == "cli_flag_added":
            return f"CLI flag `{self.subject}` was added"
        if k == "cli_flag_renamed":
            return f"CLI flag `{self.old}` was renamed to `{self.new}`"
        if k == "cli_default_changed":
            return f"default of `{self.subject}` changed from `{self.old_value}` to `{self.new_value}`"
        if k == "cli_flag_now_required":
            return f"CLI flag `{self.subject}` is now required"
        if k == "env_var_added":
            return f"environment variable `{self.subject}` was added"
        if k == "env_var_removed":
            return f"environment variable `{self.subject}` was removed"
        if k == "env_var_renamed":
            return f"environment variable `{self.old}` was renamed to `{self.new}`"
        if k == "env_default_changed":
            return f"default of `{self.subject}` changed from `{self.old_value}` to `{self.new_value}`"
        if k == "config_key_added":
            return f"config key `{self.subject}` was added"
        if k == "config_key_removed":
            return f"config key `{self.subject}` was removed"
        if k == "config_key_renamed":
            return f"config key `{self.old}` was renamed to `{self.new}`"
        return f"{k}: {self.subject}"


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _param_similarity(a: tuple[Param, ...], b: tuple[Param, ...]) -> float:
    na, nb = {p.name for p in a}, {p.name for p in b}
    if not na and not nb:
        return 1.0
    return len(na & nb) / len(na | nb)


def _rename_score(r: Symbol, a: Symbol, sole: bool) -> float:
    """Likelihood that `a` is `r` renamed: name, signature, docstring and uniqueness evidence."""
    name = _ratio(r.name, a.name)
    if name < 0.5:
        return 0.0
    doc = _ratio(r.doc, a.doc) if (r.doc or a.doc) else 0.5
    return 0.4 * name + 0.3 * _param_similarity(r.params, a.params) + 0.2 * doc + (0.1 if sole else 0.0)


def _hint(sym: Symbol | None) -> str:
    if not sym:
        return ""
    return f"{sym.name.replace('_', ' ')} {sym.doc}"


def _match_symbols(removed: dict[str, Symbol], added: dict[str, Symbol]):
    """Pair removed with added symbols. Returns (pairs, leftover_removed)."""
    pairs: list[tuple[Symbol, Symbol, float, str]] = []
    used_r: set[str] = set()
    used_a: set[str] = set()

    # moves: same name and same signature, different module
    for r in list(removed.values()):
        for a in added.values():
            if (a.qualname in used_a or r.kind != a.kind or r.name != a.name or r.module == a.module
                    or r.owner is not None or r.params != a.params):
                continue
            pairs.append((r, a, 0.95, "symbol_moved"))
            used_r.add(r.qualname)
            used_a.add(a.qualname)
            break

    # renames (classes first so their methods can be carried across)
    class_map: dict[str, str] = {}
    for kinds in (("class",), ("function", "method", "property")):
        if kinds[0] != "class":  # members of a renamed class keep their names: carried, not renamed
            for r in list(removed.values()):
                twin = added.get(f"{class_map.get(r.owner, '')}.{r.name}") if r.owner in class_map else None
                if twin and r.qualname not in used_r and twin.qualname not in used_a:
                    pairs.append((r, twin, 1.0, "carried"))
                    used_r.add(r.qualname)
                    used_a.add(twin.qualname)
        cands = []
        scope = lambda x: (x.module, x.owner, x.kind)  # noqa: E731
        n_rem = Counter(scope(r) for r in removed.values() if r.qualname not in used_r)
        n_add = Counter(scope(a) for a in added.values() if a.qualname not in used_a)
        for r in removed.values():
            if r.qualname in used_r or r.kind not in kinds:
                continue
            for a in added.values():
                if a.qualname in used_a or a.kind != r.kind or a.module != r.module:
                    continue
                owner_r = class_map.get(r.owner, r.owner)
                if owner_r != a.owner:
                    continue
                score = _rename_score(r, a, n_rem[scope(r)] == 1 and n_add[scope(a)] == 1)
                if score >= RENAME_THRESHOLD:
                    cands.append((score, r, a))
        for score, r, a in sorted(cands, key=lambda t: -t[0]):
            if r.qualname in used_r or a.qualname in used_a:
                continue
            pairs.append((r, a, score, "symbol_renamed"))
            used_r.add(r.qualname)
            used_a.add(a.qualname)
            if r.kind == "class":
                class_map[r.qualname] = a.qualname
    leftover = {q: s for q, s in removed.items() if q not in used_r}
    return pairs, leftover, class_map


def _diff_params(old: Symbol, new: Symbol, subject: str) -> list[CodeChange]:
    out: list[CodeChange] = []
    op = {p.name: p for p in old.params}
    np_ = {p.name: p for p in new.params}
    removed = [p for p in old.params if p.name not in np_]
    added = [p for p in new.params if p.name not in op]

    renamed_pairs = []
    for r in list(removed):
        idx = [p.name for p in old.params].index(r.name)
        for a in list(added):
            same_pos = idx < len(new.params) and new.params[idx].name == a.name
            if (same_pos or _ratio(r.name, a.name) >= 0.6) and r.default == a.default:
                renamed_pairs.append((r, a))
                removed.remove(r)
                added.remove(a)
                break
    for r, a in renamed_pairs:
        out.append(CodeChange("param_renamed", subject, r.name, a.name, param=r.name,
                              base_symbol=old, head_symbol=new, hint=_hint(new)))
    for r in removed:
        out.append(CodeChange("param_removed", subject, param=r.name, base_symbol=old, head_symbol=new,
                              hint=_hint(new)))
    for a in added:
        kind = "param_added_required" if a.required else "param_added_optional"
        out.append(CodeChange(kind, subject, param=a.name, base_symbol=old, head_symbol=new, hint=_hint(new)))
    for name, p in op.items():
        q = np_.get(name)
        if q and p.default != q.default and p.default is not None and q.default is not None:
            out.append(CodeChange("default_changed", subject, param=name, old_value=p.default,
                                  new_value=q.default, base_symbol=old, head_symbol=new, hint=_hint(new)))
        elif q and p.default is not None and q.default is None:
            out.append(CodeChange("param_added_required", subject, param=name, base_symbol=old,
                                  head_symbol=new, hint=_hint(new)))
    common_old = [p.name for p in old.params if p.name in np_ and p.kind != "VAR_KEYWORD"]
    common_new = [p.name for p in new.params if p.name in op and p.kind != "VAR_KEYWORD"]
    if common_old != common_new:
        out.append(CodeChange("param_reordered", subject, ", ".join(common_old), ", ".join(common_new),
                              base_symbol=old, head_symbol=new, hint=_hint(new)))
    return out


def _pair_names(removed: list[str], added: list[str], similarity, default_of, min_ratio: float = 0.6,
                blind_min: float | None = None):
    """Pair removed/added names. `similarity(r, a)` scores a pair; equal defaults are required when known.

    The returned score adds a small bonus when the pair is the only candidate on both sides.
    """
    sole = len(removed) == 1 and len(added) == 1
    pairs = []
    for r in list(removed):
        best = None
        for a in added:
            score = similarity(r, a)
            d_r, d_a = default_of(r, True), default_of(a, False)
            need = blind_min if (blind_min and d_r is None and d_a is None) else min_ratio
            if score >= need and (d_r == d_a or d_r is None or d_a is None):
                if best is None or score > best[0]:
                    best = (score, a)
        if best:
            pairs.append((r, best[1], min(1.0, best[0] + 0.1 + (0.1 if sole else 0.0))))
            removed.remove(r)
            added.remove(best[1])
    return pairs


def diff_api(base: ApiSurface, head: ApiSurface) -> list[CodeChange]:
    changes: list[CodeChange] = []

    removed = {q: s for q, s in base.symbols.items() if q not in head.symbols}
    added = {q: s for q, s in head.symbols.items() if q not in base.symbols}

    # module moves: a vanished module whose public names reappear unchanged elsewhere
    moved_modules: dict[str, str] = {}
    for old_mod, names in base.modules.items():
        if old_mod in head.modules or not names:
            continue
        for new_mod, new_names in head.modules.items():
            if new_mod not in base.modules and new_names == names:
                moved_modules[old_mod] = new_mod
                break
    for old_mod, new_mod in moved_modules.items():
        changes.append(CodeChange("module_moved", old_mod, old_mod, new_mod))
        for q in [q for q, s in removed.items() if s.module == old_mod]:
            sym = removed.pop(q)
            twin = added.pop(f"{new_mod}.{q[len(old_mod) + 1:]}", None)
            if twin:
                changes.extend(_diff_params(sym, twin, q))

    pairs, leftover, class_map = _match_symbols(removed, added)
    for r, a, score, kind in pairs:
        if kind == "carried":
            changes.extend(_diff_params(r, a, r.qualname))
            continue
        changes.append(CodeChange(kind, r.qualname, r.qualname, a.qualname, similarity=score,
                                  base_symbol=r, head_symbol=a, hint=_hint(a)))
        if r.kind != "class" or kind == "symbol_renamed":
            changes.extend(_diff_params(r, a, r.qualname))
    # methods of a renamed class carry over by name
    for q, s in list(leftover.items()):
        if s.owner in class_map:
            twin = head.symbols.get(f"{class_map[s.owner]}.{s.name}")
            if twin:
                changes.extend(_diff_params(s, twin, q))
                leftover.pop(q)
    removed_classes = {q for q, s in leftover.items() if s.kind == "class"}
    for q, s in leftover.items():
        if s.owner in removed_classes:
            continue  # reported through the class
        changes.append(CodeChange("symbol_removed", q, q, base_symbol=s, hint=_hint(s)))
    for q, s in base.symbols.items():
        if q in head.symbols:
            changes.extend(_diff_params(s, head.symbols[q], q))

    # CLI flags
    b_cli, h_cli = base.cli, head.cli
    rm = [f for f in b_cli if f not in h_cli and not any(f in o.flags for o in h_cli.values())]
    ad = [f for f in h_cli if f not in b_cli and not any(f in o.flags for o in b_cli.values())]

    def cli_default(flag, is_old):
        opt = (b_cli if is_old else h_cli)[flag]
        return opt.default

    def cli_similarity(r, a):
        helps = (b_cli[r].help, h_cli[a].help)
        by_help = _ratio(*helps) if all(helps) else 0.0
        return max(_ratio(r, a), by_help)

    for r, a, score in _pair_names(rm, ad, cli_similarity, cli_default):
        changes.append(CodeChange("cli_flag_renamed", r, r, a, similarity=score,
                                  base_cli=b_cli[r], hint=b_cli[r].help))
    for f in rm:
        changes.append(CodeChange("cli_flag_removed", f, f, base_cli=b_cli[f], hint=b_cli[f].help))
    for f in ad:
        changes.append(CodeChange("cli_flag_added", f, new=f, hint=h_cli[f].help or f))
    for f, o in b_cli.items():
        n = h_cli.get(f)
        if n and o.default != n.default and o.default is not None and n.default is not None:
            changes.append(CodeChange("cli_default_changed", f, old_value=o.default, new_value=n.default,
                                      base_cli=o, hint=o.help))
        if n and n.required and not o.required:
            changes.append(CodeChange("cli_flag_now_required", f, base_cli=o, hint=o.help))

    # environment variables
    b_env, h_env = base.env, head.env
    rm = [e for e in b_env if e not in h_env]
    ad = [e for e in h_env if e not in b_env]

    def env_default(name, is_old):
        return (b_env if is_old else h_env)[name].default

    for r, a, score in _pair_names(rm, ad, _ratio, env_default, 0.7, blind_min=0.9):
        changes.append(CodeChange("env_var_renamed", r, r, a, similarity=score, hint=r))
    for e in rm:
        changes.append(CodeChange("env_var_removed", e, e, hint=e.replace("_", " ")))
    for e in ad:
        changes.append(CodeChange("env_var_added", e, new=e, hint=e.replace("_", " ")))
    for e, o in b_env.items():
        n = h_env.get(e)
        if n and o.default != n.default and o.default is not None and n.default is not None:
            changes.append(CodeChange("env_default_changed", e, old_value=o.default, new_value=n.default,
                                      hint=e.replace("_", " ")))

    # config keys
    rm = [k for k in base.config if k not in head.config]
    ad = [k for k in head.config if k not in base.config]
    for r, a, score in _pair_names(rm, ad, _ratio, lambda k, old: None, 0.6):
        changes.append(CodeChange("config_key_renamed", r, r, a, similarity=score, hint=r))
    for k in rm:
        changes.append(CodeChange("config_key_removed", k, k, hint=k.replace("_", " ")))
    for k in ad:
        changes.append(CodeChange("config_key_added", k, new=k, hint=k.replace("_", " ")))
    return changes

"""The gnulib ports as text, for a project that carries its checks itself.

Every rule of `//gnulib/m4/*` and `//gnulib/macros/*` is lifted from its BUILD file verbatim
(`lift_file`: the source text of each attribute, the `load()`s and the module constants it
uses), so a converted project may declare the ports it depends on as targets of its own
(`convert --inline-checks`, `mirror`) and compile their checks under its own `conlyopts` and
`cxxopts`: a dependency's checks run in the dependency's configuration, which knows nothing of
the project's pins, and gnulib's gl_C_BOOL then answers for the toolchain's default mode while
the project compiles in another.  Each check's JSON is the one the port produces, so the
content cache still matches the toolchain's results wherever the flags agree.

The BUILD files are the only source: the converter lifts them from the checkout `bazel run`
started in (or the one `--index` names) at every run, so an entry carries the ports of the
tree it was generated from, the one its rules version names.  `//autoconf/tests/ports_mirror`
declares every lifted rule again through the same emitter (`bazel run
//tools/convert:update_index` writes its BUILD) and compares the evaluated attributes with the
port's, so a port the lift cannot reproduce, or a mirror that fell behind its port, fails
there.  `//autoconf/...` targets stay dependencies: they are the macro ports every project
shares.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert.emit import load_statement, quote
from tools.convert.ir import Assign, AttrValue, Dep, Package, Raw, Rule, Target

MIRRORED = ("gnulib/m4/", "gnulib/macros/")
"""The packages whose rules a project mirrors; a dependency anywhere else stays a dependency."""

_RULE_KINDS = {
    "autoconf_cache": "autoconf",
    "autoconf": "autoconf",
    "autoconf_defaults": "autoconf_defaults",
    "selects.config_setting_group": "selects.config_setting_group",
}
"""The rule symbols a port BUILD calls -> the kind the mirror is declared with.  The ports use
the toolchain's cache rule; a project's mirror is a plain `autoconf`, which consults that cache."""

_LOAD_FILES = {
    "autoconf_defaults": "//autoconf:autoconf_defaults.bzl",
    "selects.config_setting_group": "@bazel_skylib//lib:selects.bzl",
}
"""Where a mirrored rule other than `autoconf` is loaded from (`autoconf` comes with the package)."""

_CANONICAL_ALIASES = {
    ("//gnulib:macros.bzl", "macros"): "gl_macros",
    ("//gnulib:macros.bzl", "checks"): "gl_checks",
}
"""The name a loaded symbol goes by in a mirror, where the ports' files differ (one loads
gnulib's `macros` bare, which would shadow autoconf's)."""

_DEP_ATTRS = ("deps", "default_deps")
"""Label-list attributes, rewritten label by label; every other attribute is text."""

_UNPREFIXED = ("//conditions:", "//visibility:")
"""Labels of no repository: a `//` label elsewhere is this repository's and gets the repo in front."""


class LiftError(ValueError):
    """A port BUILD the lift cannot reproduce exactly."""


@dataclass
class PortRule:
    """One rule call of a port BUILD file."""

    package: str
    """The port's package (`gnulib/m4/c-bool`)."""

    kind: str
    """A key of `_RULE_KINDS`' values: `autoconf`, `autoconf_defaults`, `selects.config_setting_group`."""

    name: str
    """The rule's `name`."""

    attrs: dict[str, str] = field(default_factory=dict)
    """Attribute -> its source text, verbatim, in file order; `name` and `visibility` left out."""

    uses: list[str] = field(default_factory=list)
    """The loaded symbols and module constants the attribute texts refer to."""

    public: bool = False
    """Whether the port declares the rule `//visibility:public`: what a project may depend on."""

    @property
    def label(self) -> str:
        return f"//{self.package}:{self.name}"

    @property
    def mirror_name(self) -> str:
        """The mirror's `name`: the upstream label flattened, slashes kept (`gnulib/m4/c-bool/c-bool`)."""
        return f"{self.package}/{self.name}"


@dataclass
class PortFile:
    """A port BUILD file: its loads, module constants and rules."""

    package: str
    """The package (`gnulib/m4/inttypes`)."""

    loads: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Local alias -> (file label, symbol) of every `load()`; a package-relative file is absolute."""

    constants: dict[str, str] = field(default_factory=dict)
    """Module-level `NAME = value`: name -> the value's source text."""

    constant_uses: dict[str, list[str]] = field(default_factory=dict)
    """Constant -> the loaded symbols and constants its value refers to."""

    rules: list[PortRule] = field(default_factory=list)
    """The rule calls, in file order."""

    @property
    def rule_names(self) -> set[str]:
        return {rule.name for rule in self.rules}


def lift_file(package: str, text: str) -> PortFile:
    """The rules of a port BUILD file, each attribute as the text it is written as.

    Raises `LiftError` for anything but a docstring, `load()`s, `NAME = value` constants and
    keyword-only rule calls with a literal `name`: the lift reproduces, it never approximates.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise LiftError(f"//{package}:BUILD.bazel: {error}") from error
    file = PortFile(package=package)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # the docstring
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "load":
                _lift_load(file, call)
            else:
                file.rules.append(_lift_rule(file, text, call))
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            segment = ast.get_source_segment(text, node.value)
            if segment is None:
                raise LiftError(f"//{package}:BUILD.bazel: no source for {name}")
            file.constants[name] = segment
            file.constant_uses[name] = _names_used(node.value, file)
            continue
        raise LiftError(f"//{package}:BUILD.bazel:{node.lineno}: a statement the lift does not carry")
    return file


def _lift_load(file: PortFile, call: ast.Call) -> None:
    if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
        raise LiftError(f"//{file.package}:BUILD.bazel: load() without a literal file")
    label = call.args[0].value
    if label.startswith(":"):
        label = f"//{file.package}{label}"
    for arg in call.args[1:]:
        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
            raise LiftError(f"//{file.package}:BUILD.bazel: load() with a non-literal symbol")
        file.loads[arg.value] = (label, arg.value)
    for keyword in call.keywords:
        if (
            keyword.arg is None
            or not isinstance(keyword.value, ast.Constant)
            or not isinstance(keyword.value.value, str)
        ):
            raise LiftError(f"//{file.package}:BUILD.bazel: load() with a non-literal symbol")
        file.loads[keyword.arg] = (label, keyword.value.value)


def _lift_rule(file: PortFile, text: str, call: ast.Call) -> PortRule:
    fn = ast.unparse(call.func)
    symbol = file.loads[fn][1] if fn in file.loads else fn
    kind = _RULE_KINDS.get(symbol)
    if kind is None:
        raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: a {fn}() rule the lift does not carry")
    if call.args:
        raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: {fn}() with positional arguments")
    name = None
    attrs: dict[str, str] = {}
    uses: set[str] = set()
    public = False
    for keyword in call.keywords:
        if keyword.arg == "name":
            if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, str):
                raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: {fn}() with a computed name")
            name = keyword.value.value
            continue
        if keyword.arg == "visibility":
            try:
                visibility = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError) as error:
                raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: computed visibility") from error
            public = isinstance(visibility, list) and "//visibility:public" in visibility
            continue
        if keyword.arg is None:
            raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: {fn}(**kwargs)")
        segment = ast.get_source_segment(text, keyword.value)
        if segment is None:
            raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: no source for {keyword.arg}")
        attrs[keyword.arg] = segment
        uses.update(_names_used(keyword.value, file))
    if name is None:
        raise LiftError(f"//{file.package}:BUILD.bazel:{call.lineno}: {fn}() without a name")
    return PortRule(package=file.package, kind=kind, name=name, attrs=attrs, uses=sorted(uses), public=public)


def _names_used(node: ast.AST, file: PortFile) -> list[str]:
    """The loaded symbols and constants an expression refers to (`checks.AC_DEFINE` uses `checks`)."""
    known = set(file.loads) | set(file.constants)
    return sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in known})


def lift_workspace(root: Path) -> dict[str, PortFile]:
    """Every mirrored package of a checkout, by package path."""
    files: dict[str, PortFile] = {}
    for base in MIRRORED:
        directory = root / base.rstrip("/")
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            build = entry / "BUILD.bazel"
            if entry.is_dir() and build.is_file():
                package = f"{base}{entry.name}"
                files[package] = lift_file(package, build.read_text(encoding="utf-8"))
    return files


class PortSnapshot:
    """The lifted ports of one checkout, by package and by label."""

    def __init__(self, files: Iterable[PortFile]):
        self.files: dict[str, PortFile] = {f.package: f for f in files}
        self.rules: dict[str, PortRule] = {r.label: r for f in self.files.values() for r in f.rules}

    @classmethod
    def from_workspace(cls, root: Path) -> PortSnapshot:
        return cls(lift_workspace(root).values())

    def resolve(self, label: str) -> str:
        """A dependency label as a port spells it -> the rule's label (`//gnulib/m4/c-bool` is
        the package's target of its own name)."""
        if "//" in label:
            label = "//" + label.split("//", 1)[1]
        package, _, name = label[2:].partition(":")
        return f"//{package}:{name or package.rsplit('/', 1)[-1]}"


def is_mirrored(label: str) -> bool:
    """Whether a dependency label names a port a project mirrors."""
    if "//" not in label:
        return False
    package = label.split("//", 1)[1].partition(":")[0]
    return package.startswith(MIRRORED)


@dataclass
class Mirrored:
    """What a package declares to carry a set of ports itself (`mirror`)."""

    constants: list[Assign] = field(default_factory=list)
    """The ports' module constants, renamed after their package."""

    targets: list[Target] = field(default_factory=list)
    """The `autoconf` mirrors; the package's standard pins apply to them as to its own targets."""

    rules: list[Rule] = field(default_factory=list)
    """The `autoconf_defaults` and config-setting mirrors."""

    loads: list[str] = field(default_factory=list)
    """`load()` statements the mirrors' texts need beyond the package's own."""

    labels: dict[str, str] = field(default_factory=dict)
    """Port label -> the mirror's label (`:gnulib/m4/c-bool/c-bool`)."""


def mirror(ports: PortSnapshot, roots: Iterable[str], *, repo: str, local: str = ":") -> Mirrored:
    """The ports reachable from `roots` (dependency labels, as a project spells them), each as a
    rule of the calling package: `autoconf` mirrors of the checks, `autoconf_defaults` mirrors of
    the defaults, config-setting mirrors of the selects' keys.  Labels among the mirrored
    packages become the mirrors' (`local` in front of the flattened name); every other label
    gets `repo` in front, as a dependency of the project.  Attribute texts are the ports' own,
    with the ports' local labels, load aliases and constants renamed to the package's."""
    out = Mirrored()
    queue = [ports.resolve(label) for label in roots if is_mirrored(label)]
    seen: set[str] = set()
    constants_done: set[tuple[str, str]] = set()
    load_lines: set[str] = set()

    def label_for(label: str) -> str:
        if not is_mirrored(label):
            return f"{repo}{label}" if label.startswith("//") and not label.startswith(_UNPREFIXED) else label
        resolved = ports.resolve(label)
        if resolved not in ports.rules:
            raise LiftError(f"{label}: no such port rule in the checkout")
        if resolved not in seen:
            queue.append(resolved)
        return f"{local}{ports.rules[resolved].mirror_name}"

    while queue:
        label = queue.pop(0)
        if label in seen:
            continue
        seen.add(label)
        rule = ports.rules.get(label)
        if rule is None:
            raise LiftError(f"{label}: no such port rule in the checkout")
        file = ports.files[rule.package]
        out.labels[label] = f"{local}{rule.mirror_name}"
        names, needed_constants, needed_loads = _renames(file, rule.uses)
        package = file.package

        def labels(label: str, package: str = package) -> str:
            return label_for(_absolute(package, label))

        for constant in needed_constants:
            constant_key = (file.package, constant)
            if constant_key in constants_done:
                continue
            constants_done.add(constant_key)
            c_names, _, c_loads = _renames(file, file.constant_uses.get(constant, []))
            needed_loads |= c_loads
            out.constants.append(
                Assign(
                    _constant_name(file.package, constant),
                    Raw(_rewrite(file.constants[constant], c_names, labels)),
                    comments=[f"{repo}//{file.package}:BUILD.bazel"],
                )
            )
        load_lines |= {load_statement(path, [entry], repo=repo) for path, entry in needed_loads}
        attrs = {key: _rewrite(text, names, labels) for key, text in rule.attrs.items() if key not in _DEP_ATTRS}
        deps = {key: _dep_attr(rule, key, labels) for key in _DEP_ATTRS if key in rule.attrs}
        comment = f"{repo}{rule.label}"
        if rule.kind == "autoconf":
            plain_deps, deps_text = deps.get("deps", ([], ""))
            plain_defaults, defaults_text = deps.get("default_deps", ([], ""))
            target = Target(
                name=rule.mirror_name,
                checks=[Raw(attrs.pop("checks"))] if "checks" in attrs else [],
                deps=[Dep(d) for d in plain_deps or []],
                deps_suffix=[deps_text] if plain_deps is None else [],
                default_deps=[Dep(d) for d in plain_defaults or []],
                default_deps_suffix=[defaults_text] if plain_defaults is None else [],
                comments=[comment],
                visibility="//visibility:private",
                helper=True,
                attrs={key: Raw(text) for key, text in attrs.items()},
            )
            out.targets.append(target)
        else:
            rule_attrs: dict[str, AttrValue] = {key: Raw(text) for key, text in attrs.items()}
            for key, (plain, text) in deps.items():
                rule_attrs[key] = list(plain) if plain is not None else Raw(text)
            rule_attrs["visibility"] = ["//visibility:private"]
            out.rules.append(
                Rule(
                    rule.kind,
                    rule.mirror_name,
                    attrs=rule_attrs,
                    comments=[comment],
                    load_from=_LOAD_FILES[rule.kind],
                    load_symbol=rule.kind.split(".")[0] if "." in rule.kind else None,
                )
            )
    out.loads = sorted(load_lines)
    return out


def _dep_attr(rule: PortRule, key: str, labels: Callable[[str], str]) -> tuple[list[str] | None, str]:
    """A `deps` / `default_deps` attribute: the labels, mapped, when it is a literal list (None
    otherwise: a list plus a `select()`), and the attribute's text with every label mapped."""
    text = rule.attrs[key]
    rewritten = _rewrite(text, {}, labels)
    try:
        value = ast.literal_eval(text)
    except ValueError, SyntaxError:
        return None, rewritten
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None, rewritten
    return [labels(item) for item in value], rewritten


def _absolute(package: str, label: str) -> str:
    """A label as a port spells it -> absolute (`:defaults` of `gnulib/m4/x` is `//gnulib/m4/x:defaults`)."""
    return f"//{package}{label}" if label.startswith(":") else label


_LABEL_RE = re.compile(r"(@[\w~.-]*)?//[\w./+-]*(:[\w./+-]+)?|:[\w./+-]+")
"""A string literal that is a label: `:x`, `//pkg`, `//pkg:x`, `@repo//pkg:x` (`@VAR@` and C
text are not)."""


def _is_label(value: str) -> bool:
    return _LABEL_RE.fullmatch(value) is not None


def _constant_name(package: str, name: str) -> str:
    """A port's module constant renamed after its package, in the case the port spells it
    (buildifier wants UPPER_SNAKE_CASE or lower_snake_case throughout): `_DECL_INCLUDES` of
    `gnulib/m4/x` is `_GNULIB_M4_X_DECL_INCLUDES`, `_fu_cv_sys_stat_statvfs_include` of
    `gnulib/m4/fsusage` is `_gnulib_m4_fsusage_fu_cv_sys_stat_statvfs_include`."""
    slug = "".join(c if c.isalnum() else "_" for c in package)
    bare = name.lstrip("_")
    slug = slug.upper() if bare == bare.upper() else slug.lower()
    return f"_{slug}_{bare}" if name.startswith("_") else f"{slug}_{name}"


def _renames(file: PortFile, uses: Iterable[str]) -> tuple[dict[str, str], list[str], set[tuple[str, str]]]:
    """For an attribute text of `file`: the identifier renames (load aliases to their canonical
    names, constants to their package-qualified names), the constants it needs and the
    `load()`s (file, entry) it needs."""
    names: dict[str, str] = {}
    constants: list[str] = []
    loads: set[tuple[str, str]] = set()
    pending = list(uses)
    done: set[str] = set()
    while pending:
        use = pending.pop()
        if use in done:
            continue
        done.add(use)
        if use in file.constants:
            constants.append(use)
            names[use] = _constant_name(file.package, use)
            pending.extend(file.constant_uses.get(use, []))
            continue
        if use in file.loads:
            path, symbol = file.loads[use]
            if symbol in _RULE_KINDS:
                continue  # the rule symbols come with the package's own loads
            canonical = _CANONICAL_ALIASES.get((path, symbol), symbol)
            if canonical != use:
                names[use] = canonical
            loads.add((path, quote(symbol) if canonical == symbol else f"{canonical} = {quote(symbol)}"))
    return names, sorted(constants), loads


def _rewrite(text: str, names: dict[str, str], labels: Callable[[str], str]) -> str:
    """`text` with every label literal passed through `labels` and every identifier in `names`
    renamed, token by token; everything else, string contents included, byte for byte."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError) as error:
        raise LiftError(f"cannot tokenize {text[:40]!r}: {error}") from error
    lines = text.splitlines(keepends=True)
    edits: list[tuple[tuple[int, int], tuple[int, int], str]] = []
    previous = None
    for token in tokens:
        if token.type == tokenize.STRING and token.start[0] == token.end[0]:
            try:
                value = ast.literal_eval(token.string)
            except ValueError, SyntaxError:
                value = None
            if isinstance(value, str) and _is_label(value):
                replacement = labels(value)
                if replacement != value:
                    edits.append((token.start, token.end, quote(replacement)))
        elif token.type == tokenize.NAME and token.string in names:
            if not (previous is not None and previous.type == tokenize.OP and previous.string == "."):
                edits.append((token.start, token.end, names[token.string]))
        previous = token
    for (row, col), (_, end), replacement in reversed(edits):
        line = lines[row - 1]
        lines[row - 1] = line[:col] + replacement + line[end:]
    return "".join(lines)


MIRROR_TEST_PACKAGE = "autoconf/tests/ports_mirror"
"""The package that declares every lifted rule again and compares it with the port."""


def mirror_test_package(ports: PortSnapshot, command: str) -> Package:
    """The BUILD of `MIRROR_TEST_PACKAGE`: a mirror of every port rule, as `mirror` declares them
    for a project (in-repo labels), and one `ports_mirror_test` over the public ones.  Rendered
    by `update_index`, so the test exercises the emitter the converter uses."""
    mirrored = mirror(ports, sorted(ports.rules), repo="", local=":")
    # The test names the public rules, what a project may depend on; the private ones (a
    # port's `:defaults`, its helpers, its config settings) are reached through them, as the
    # mirrors reach theirs, and compared along the way.
    public = {label for label, rule in ports.rules.items() if rule.public}
    package = Package(
        path=MIRROR_TEST_PACKAGE,
        docstring=(
            "# @generated: every rule of the gnulib ports, declared again as `convert --inline-checks`\n"
            "# declares it for a project, and compared with the port it mirrors.\n"
            f"# Regenerate with `{command}`; do not edit."
        ),
    )
    package.before.extend(mirrored.constants)
    package.targets.extend(mirrored.targets)
    package.after.extend(mirrored.rules)
    package.loads.extend(mirrored.loads)
    pairs = sorted((port, label) for port, label in mirrored.labels.items() if port in public)
    package.after.append(
        Rule(
            "ports_mirror_test",
            "ports_mirror_test",
            attrs={
                "mirrors": [mirror_label for _, mirror_label in pairs],
                "ports": [port_label for port_label, _ in pairs],
            },
            comments=[
                "Each public port's evaluated attributes against its mirror's, and those of every rule",
                "reached through their deps and default_deps (ports_mirror_test.bzl).",
            ],
            load_from=":ports_mirror_test.bzl",
        )
    )
    return package

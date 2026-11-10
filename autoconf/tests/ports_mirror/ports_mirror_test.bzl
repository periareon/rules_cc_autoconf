"""Each gnulib port against its mirror, as `convert --inline-checks` declares it for a project.

The generated BUILD.bazel beside this file (`bazel run //tools/convert:update_index`) declares
every port rule again from the lifted text of its BUILD file, with the ports' labels rewritten
to the mirrors'.  The test names the public ports and their mirrors; an aspect walks `deps`
and `default_deps` from each, so a port's private `:defaults`, helpers and the mirrors of
those are compared too.  Both sides are read after Bazel evaluated them, in one
configuration: the checks (their JSON), the defaults' tables, and the dependencies with the
mirrors' labels mapped back to the ports'.  A port the lift cannot reproduce fails here
before it reaches an entry.
"""

_LiftedInfo = provider(
    doc = "A rule's evaluated attributes, as the comparison reads them, with its transitive deps'.",
    fields = {
        "rules": "depset of struct(label, kind, checks, tables, deps): this rule and every rule reached.",
    },
)

def _plain(label):
    """A label as a string without the main repository's `@@` (Bazel spells `//x:y` as `@@//x:y`)."""
    text = str(label)
    if text.startswith("@@//"):
        return text[2:]
    if text.startswith("@//"):
        return text[1:]
    return text

def _lifted_aspect_impl(target, ctx):
    attr = ctx.rule.attr
    direct = [d for d in getattr(attr, "deps", [])] + [d for d in getattr(attr, "default_deps", [])]

    # A depset element must be immutable: tuples throughout.
    tables = []
    for name in ("substs", "variables", "defines"):
        value = getattr(attr, name, None)
        if value != None:
            tables.append((name, tuple(sorted(value.items()))))
    info = struct(
        label = _plain(target.label),
        kind = ctx.rule.kind,
        checks = tuple(getattr(attr, "checks", []) or []),
        tables = tuple(tables),
        deps = tuple([_plain(d.label) for d in direct]),
    )
    return [_LiftedInfo(rules = depset(
        [info],
        transitive = [d[_LiftedInfo].rules for d in direct if _LiftedInfo in d],
    ))]

_lifted_aspect = aspect(
    implementation = _lifted_aspect_impl,
    attr_aspects = ["deps", "default_deps"],
)

# The kinds a mirror may have for a port's: the ports use the toolchain's cache rule, a project
# the plain one.
_SAME_KIND = {"autoconf_cache": "autoconf"}

_MIRROR_PACKAGE = "//autoconf/tests/ports_mirror:"

def _port_of(mirror_label):
    """The port a mirror stands for.

    `//autoconf/tests/ports_mirror:gnulib/m4/x/y` is `//gnulib/m4/x:y`; any other label is
    itself (a dependency outside the mirrored packages).
    """
    if not mirror_label.startswith(_MIRROR_PACKAGE):
        return mirror_label
    name = mirror_label[len(_MIRROR_PACKAGE):]
    parts = name.split("/", 3)
    if len(parts) != 4:
        return mirror_label
    return "//{}:{}".format("/".join(parts[:3]), parts[3])

def _compare(port, mirror, failures):
    where = "{} vs {}".format(port.label, mirror.label)
    if _SAME_KIND.get(port.kind, port.kind) != mirror.kind:
        failures.append("{}: kind {} vs {}".format(where, port.kind, mirror.kind))
    if port.checks != mirror.checks:
        failures.append("{}: checks differ\n  port:   {}\n  mirror: {}".format(where, port.checks, mirror.checks))
    if port.tables != mirror.tables:
        failures.append("{}: defaults differ\n  port:   {}\n  mirror: {}".format(where, port.tables, mirror.tables))
    mapped = sorted([_port_of(d) for d in mirror.deps])
    if sorted(port.deps) != mapped:
        failures.append("{}: deps differ\n  port:   {}\n  mirror: {}".format(where, sorted(port.deps), mapped))

def _ports_mirror_test_impl(ctx):
    if len(ctx.attr.ports) != len(ctx.attr.mirrors):
        fail("ports and mirrors must pair up")
    ports = {}
    mirrors = {}
    for port, mirror in zip(ctx.attr.ports, ctx.attr.mirrors):
        for info in port[_LiftedInfo].rules.to_list():
            ports[info.label] = info
        for info in mirror[_LiftedInfo].rules.to_list():
            mirrors[_port_of(info.label)] = info
    failures = []
    compared = 0
    for label, info in ports.items():
        if not label.startswith("//gnulib/"):
            continue  # a shared macro port: a dependency of the mirrors too, never mirrored
        mirror = mirrors.get(label)
        if mirror == None:
            failures.append("{}: no mirror reached".format(label))
            continue
        compared += 1
        _compare(info, mirror, failures)

    # The verdict is a file the script prints: the differences may hold any character, and a
    # Windows batch file could not quote them.
    report = ctx.actions.declare_file(ctx.label.name + ".report.txt")
    if failures:
        ctx.actions.write(report, "\n".join(failures) + "\n")
    else:
        ctx.actions.write(report, "{} port rules match their mirrors\n".format(compared))
    status = 1 if failures else 0
    windows = ctx.target_platform_has_constraint(ctx.attr._windows_constraint[platform_common.ConstraintValueInfo])
    if windows:
        script = ctx.actions.declare_file(ctx.label.name + ".bat")
        body = "@echo off\r\ntype \"%~dp0{}\"\r\nexit /b {}\r\n".format(report.basename, status)
    else:
        script = ctx.actions.declare_file(ctx.label.name + ".sh")
        body = "#!/bin/bash\ncat \"$(dirname \"$0\")/{}\"\nexit {}\n".format(report.basename, status)
    ctx.actions.write(script, body, is_executable = True)
    return [DefaultInfo(executable = script, runfiles = ctx.runfiles(files = [report]))]

ports_mirror_test = rule(
    doc = "Compares each port's evaluated attributes with its mirror's, transitively; fails naming every difference.",
    implementation = _ports_mirror_test_impl,
    attrs = {
        "mirrors": attr.label_list(
            doc = "The mirrors of the public ports, in the order of `ports`.",
            aspects = [_lifted_aspect],
            mandatory = True,
        ),
        "ports": attr.label_list(
            doc = "The public port rules.",
            aspects = [_lifted_aspect],
            mandatory = True,
        ),
        "_windows_constraint": attr.label(
            default = "@platforms//os:windows",
        ),
    },
    test = True,
)

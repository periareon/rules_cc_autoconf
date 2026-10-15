"""# cc_gnulib_conditional_hdrs"""

load("@bazel_features//:features.bzl", "bazel_features")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

# buildifier: disable=bzl-visibility
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "get_autoconf_toolchain_defaults",
)

# buildifier: disable=bzl-visibility
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars")

# buildifier: disable=bzl-visibility
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load("//gnulib/private:result_lookup.bzl", "find_result_file")

def _package_relative(hdr_file, ctx):
    """The header's path relative to this rule's package, or None when it is in another package.

    `short_path` of a file in an external repository is `../<repo>/<path>`;
    the root package is the empty string.
    """
    short = hdr_file.short_path
    if short.startswith("../"):
        short = short.split("/", 2)[2]
    pkg = ctx.label.package
    if not pkg:
        return short
    if short.startswith(pkg + "/"):
        return short[len(pkg) + 1:]
    return None

def _slot_name(hdr_file, ctx):
    """Where a header lands in the TreeArtifact: what consumers `#include`."""
    prefix = ctx.attr.strip_include_prefix.strip("/")
    if prefix:
        relative = _package_relative(hdr_file, ctx)
        if relative == None or not relative.startswith(prefix + "/"):
            fail("`{}` in hdrs of `{}` is not under strip_include_prefix `{}/` of its package".format(
                hdr_file.short_path,
                ctx.label,
                prefix,
            ))
        return relative[len(prefix) + 1:]
    if ctx.attr.preserve_paths:
        relative = _package_relative(hdr_file, ctx)
        return relative if relative != None else hdr_file.basename
    return hdr_file.basename

def _cc_gnulib_conditional_hdrs_impl(ctx):
    if ctx.attr.strip_include_prefix and ctx.attr.preserve_paths:
        fail("`{}`: strip_include_prefix and preserve_paths are mutually exclusive".format(ctx.label))
    deps = collect_deps(ctx.attr.deps)
    dep_results = collect_transitive_results(deps.to_list())

    # Toolchain defaults first, so a dependency's result overrides them
    # (as autoconf_srcs and autoconf_hdr do).
    defaults = struct(cache = {}, define = {}, subst = {})
    if ctx.attr.defaults:
        defaults = get_autoconf_toolchain_defaults(ctx)
    all_cache = defaults.cache | dep_results["cache"]
    all_define = defaults.define | dep_results["define"]
    all_subst = defaults.subst | dep_results["subst"]

    tree_name = ctx.attr.out
    if not tree_name:
        tree_name = ctx.label.name
    tree = ctx.actions.declare_directory(tree_name)

    args = ctx.actions.args()
    args.add_all([tree], before_each = "--out-dir", expand_directories = False)

    inputs = []
    seen_dep_names = {}
    seen_slots = {}

    for hdr, condition in ctx.attr.hdrs.items():
        files = hdr.files.to_list()
        if len(files) != 1:
            fail("Label `{}` in hdrs of `{}` must produce exactly one file, got {}".format(
                hdr.label,
                ctx.label,
                len(files),
            ))

        hdr_file = files[0]
        slot = _slot_name(hdr_file, ctx)

        existing = seen_slots.get(slot)
        if existing != None:
            fail(("Duplicate output slot `{}` in `{}` (headers `{}` and `{}` " +
                  "both resolve to the same TreeArtifact path).").format(
                slot,
                ctx.label,
                existing,
                hdr.label,
            ))
        seen_slots[slot] = hdr.label

        for var in extract_condition_vars(condition):
            result_file = find_result_file(
                var,
                all_cache,
                all_define,
                all_subst,
                ctx.label,
            )
            if var not in seen_dep_names:
                args.add_all([result_file], before_each = "--dep", format_each = "{}=%s".format(var))
                inputs.append(result_file)
                seen_dep_names[var] = result_file.path
            elif seen_dep_names[var] != result_file.path:
                fail("Internal: `{}` resolved to two different files for `{}`".format(
                    var,
                    ctx.label,
                ))

        args.add_all([hdr_file], before_each = "--hdr", format_each = "%s,{},{}".format(condition, slot))
        inputs.append(hdr_file)

    ctx.actions.run(
        toolchain = None,
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = inputs,
        outputs = [tree],
        mnemonic = "CcGnulibConditionalHdrs",
        # TODO: https://github.com/periareon/rules_cc_autoconf/issues/148
        execution_requirements = {"supports-path-mapping": ""} if bazel_features.rules.write_action_has_execution_requirements else {},
    )

    compilation_context = cc_common.create_compilation_context(
        includes = depset([tree.path]),
        headers = depset([tree]),
    )

    return [
        DefaultInfo(
            files = depset([tree]),
        ),
        CcInfo(
            compilation_context = compilation_context,
        ),
    ]

cc_gnulib_conditional_hdrs = rule(
    implementation = _cc_gnulib_conditional_hdrs_impl,
    doc = """\
Gate gnulib replacement headers on per-header autoconf conditions, mirroring
upstream gnulib's ``GL_GENERATE_*_H`` / ``rm -f $@`` pattern.

For each truthy entry in ``hdrs`` the file is written into the rule's
TreeArtifact output directory under its slot name (see ``preserve_paths``
below); for each falsy entry no file is written, so consumer ``#include``s
of that basename fall through the rest of the include search path to
whatever comes next (typically the system header).

The rule returns ``CcInfo`` exposing the TreeArtifact directory as a
single include root — drop the rule into a ``cc_library``'s ``deps`` and
consumers can ``#include`` directly:

```python
load("@rules_cc_autoconf//gnulib:conditional_hdrs.bzl", "cc_gnulib_conditional_hdrs")

cc_gnulib_conditional_hdrs(
    name = "gnu_replacements",
    hdrs = {
        ":alloca_h_processed":       "GL_GENERATE_ALLOCA_H",
        ":obstack_h_processed":      "GL_GENERATE_OBSTACK_H",
        ":getopt_cdefs_h_processed": "GL_GENERATE_GETOPT_CDEFS_H",
    },
    deps = [":autoconf"],
)

cc_library(
    name = "consumer",
    srcs = ["uses_alloca.c"],
    deps = [":gnu_replacements"],
)
```

The falsy branch emits no file, true to upstream's ``rm -f $@``: any
``#include_next`` chain a ``.in.h`` template needs is baked into the header
by the upstream ``autoconf_hdr`` substitution, so this rule never emits
``#include_next`` itself and has no MSVC gap.

Headers in subdirectories (``selinux/context.h``) are gated through
``strip_include_prefix``: declare them under one directory and strip it, as
``cc_library`` does.

```python
cc_gnulib_conditional_hdrs(
    name = "gnu_replacements",
    hdrs = {
        ":_gnulib_cond/selinux/context.h": "GL_GENERATE_SELINUX_CONTEXT_H",
    },
    strip_include_prefix = "_gnulib_cond",
    deps = [":autoconf"],
)
```

Limitations:

  - *Header-name collisions.* The TreeArtifact directory is added to the
    include path.  Any consumer with a same-named header in an earlier
    include dir will shadow the replacement.
  - *TreeArtifact semantics.* Bazel cannot statically know which slot
    names will be present (truthy is a configure-time decision).  This
    is intentional and matches upstream behavior.
  - *``preserve_paths`` shadowing on non-sandboxed builds (Windows).* With
    the deprecated ``preserve_paths = True`` the TreeArtifact contents path
    equals the upstream ``autoconf_hdr`` ``out`` path verbatim, and on a
    non-sandboxed build a ``cc_library(includes = ["."])`` elsewhere in the
    graph may resolve an ``#include`` to the un-gated intermediate.
    ``strip_include_prefix`` is immune: the stripped directory never
    coincides with the slot.
""",
    attrs = {
        "defaults": attr.bool(
            doc = """\
Whether the conditions may also read the autoconf toolchain's defaults
(the ``GL_GENERATE_*_H`` of header packages a project never configures
itself).  Results from ``deps`` take precedence.""",
            default = False,
        ),
        "deps": attr.label_list(
            doc = """\
Autoconf targets providing check results.  The condition expressions in
``hdrs`` are resolved against the merged results from all deps.""",
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "hdrs": attr.label_keyed_string_dict(
            doc = """\
Mapping of header file label to a condition expression (same syntax as
``autoconf_srcs``: single var, ``!var``, ``HAVE_X==1``, ``A && !B``…).
When the condition holds the header is written into the TreeArtifact at
its slot name; when it does not hold nothing is written for that header
and consumer ``#include``s fall through the include search path.""",
            allow_files = True,
            mandatory = True,
            allow_empty = False,
        ),
        "out": attr.string(
            doc = "The name of the output directory. If unset, `{name}` will be used.",
            default = "",
        ),
        "preserve_paths": attr.bool(
            doc = """\
Deprecated: use ``strip_include_prefix``.  If ``True``, each header is placed
at its path relative to the rule's package (``_gnulib/foo.h``), so consumers
``#include <_gnulib/foo.h>``; headers from other packages use their basename.
Mutually exclusive with ``strip_include_prefix``.""",
            default = False,
        ),
        "strip_include_prefix": attr.string(
            doc = """\
A directory of this package to strip from each header's package-relative
path to get its slot, as ``cc_library`` does: with ``_gnulib_cond`` the
header ``_gnulib_cond/selinux/context.h`` is included as
``<selinux/context.h>``.  Every header in ``hdrs`` must be under it.  Unset,
each header is placed at its basename.""",
            default = "",
        ),
        "_runner": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//gnulib/private/conditional_hdrs"),
        ),
    },
    provides = [CcInfo],
    toolchains = [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
)

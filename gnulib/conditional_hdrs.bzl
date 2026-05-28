"""# cc_gnulib_conditional_hdrs"""

load("@bazel_features//:features.bzl", "bazel_features")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")

# buildifier: disable=bzl-visibility
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
)

# buildifier: disable=bzl-visibility
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars")

# buildifier: disable=bzl-visibility
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load("//gnulib/private:result_lookup.bzl", "find_result_file")

def _slot_name(hdr_file, ctx):
    if not ctx.attr.preserve_paths:
        return hdr_file.basename

    pkg = ctx.label.package
    short = hdr_file.short_path
    if pkg and short.startswith(pkg + "/"):
        return short[len(pkg) + 1:]
    return hdr_file.basename

def _cc_gnulib_conditional_hdrs_impl(ctx):
    deps = collect_deps(ctx.attr.deps)
    dep_results = collect_transitive_results(deps.to_list())

    all_cache = dep_results["cache"]
    all_define = dep_results["define"]
    all_subst = dep_results["subst"]

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

Unlike the deprecated ``gnulib_conditional_hdr``, this rule does not need
``INCLUDE_NEXT`` / ``NEXT_*_H``: the falsy branch emits no file (true to
upstream's ``rm -f $@``), and any ``#include_next`` chain a ``.in.h``
template needs is baked into the header by the upstream ``autoconf_hdr``
substitution.  As a side effect, this rule has no MSVC ``#include_next``
gap — it never emits ``#include_next`` directly.

Limitations:

  - *Header-name collisions.* The TreeArtifact directory is added to the
    include path.  Any consumer with a same-named header in an earlier
    include dir will shadow the replacement.
  - *TreeArtifact semantics.* Bazel cannot statically know which slot
    names will be present (truthy is a configure-time decision).  This
    is intentional and matches upstream behavior.
  - *Preserve-mode shadowing on non-sandboxed builds (Windows).* With
    ``preserve_paths = True``, the TreeArtifact contents path matches
    the upstream ``autoconf_hdr`` ``out`` path verbatim.  On sandboxed
    builds the intermediate is not an input to the consumer and so is
    invisible.  On non-sandboxed builds the intermediate is physically
    present in ``bazel-bin``; if another rule in the consumer's graph
    exposes ``bazel-bin/<pkg>/`` to ``-I`` (e.g.
    ``cc_library(includes = ["."])``), an ``#include`` may resolve to
    the un-gated intermediate instead of the rule's gated copy.
    Mitigations: pick an ``autoconf_hdr`` ``out`` path whose top-level
    segment differs from what consumers ``#include`` (e.g.
    ``_processed/_gnulib/foo.h``), or stay on the ``preserve_paths =
    False`` default, which is collision-immune by construction (the
    intermediate's path and the TreeArtifact contents path never
    coincide).
""",
    attrs = {
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
If ``False`` (default), each header is placed at ``basename(hdr)`` inside
the TreeArtifact, matching gnulib's ``rm -f $@`` semantics: consumers
``#include <foo.h>`` and fall through to the system header on the falsy
branch.  If ``True``, each header is placed at its path relative to the
rule's package (e.g. ``_gnulib/foo.h``); consumers then
``#include <_gnulib/foo.h>``.  Headers from other packages always use
basename.""",
            default = False,
        ),
        "_runner": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//gnulib/private/conditional_hdrs"),
        ),
    },
    provides = [CcInfo],
)

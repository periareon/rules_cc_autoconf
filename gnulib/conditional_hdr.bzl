"""Conditional header generation matching gnulib's gl_CONDITIONAL_HEADER pattern."""

# buildifier: disable=bzl-visibility
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
)

# buildifier: disable=bzl-visibility
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _find_result_file(name, all_cache, all_define, all_subst, label):
    """Look up a check result name across cache/define/subst namespaces.

    Returns the result File, or fails with a clear error if not found or
    ambiguous.
    """
    candidates = []
    if name in all_cache:
        candidates.append(("cache", all_cache[name]))
    if name in all_define:
        candidates.append(("define", all_define[name]))
    if name in all_subst:
        candidates.append(("subst", all_subst[name]))

    if not candidates:
        all_available = {
            "cache": sorted(all_cache.keys()),
            "define": sorted(all_define.keys()),
            "subst": sorted(all_subst.keys()),
        }
        fail("gnulib_conditional_hdr `{}` requires `{}` which is not provided by any deps. Available: {}".format(
            label,
            name,
            json.encode_indent(all_available, indent = " " * 4),
        ))

    distinct_paths = {}
    for bucket, f in candidates:
        distinct_paths[f.path] = (bucket, f)

    if len(distinct_paths) != 1:
        fail("gnulib_conditional_hdr `{}` requires `{}` but it is ambiguous across deps.\nMatches: {}".format(
            label,
            name,
            [(bucket, f.path) for (bucket, f) in candidates],
        ))

    return distinct_paths.values()[0][1]

def _gnulib_conditional_hdr_impl(ctx):
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    all_cache = dep_results["cache"]
    all_define = dep_results["define"]
    all_subst = dep_results["subst"]

    condition_file = _find_result_file(ctx.attr.condition, all_cache, all_define, all_subst, ctx.label)
    include_next_file = _find_result_file(ctx.attr.include_next, all_cache, all_define, all_subst, ctx.label)
    next_header_file = _find_result_file(ctx.attr.next_header, all_cache, all_define, all_subst, ctx.label)

    src_file = ctx.file.src

    args = ctx.actions.args()
    args.add("--src", src_file)
    args.add("--output", ctx.outputs.out)
    args.add("--dep", "{}={}".format(ctx.attr.condition, condition_file.path))
    args.add("--dep", "{}={}".format(ctx.attr.include_next, include_next_file.path))
    args.add("--dep", "{}={}".format(ctx.attr.next_header, next_header_file.path))
    args.add("--condition", ctx.attr.condition)
    args.add("--include-next", ctx.attr.include_next)
    args.add("--next-header", ctx.attr.next_header)

    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = [src_file, condition_file, include_next_file, next_header_file],
        outputs = [ctx.outputs.out],
        mnemonic = "GnulibConditionalHdr",
    )

    return [DefaultInfo(files = depset([ctx.outputs.out]))]

gnulib_conditional_hdr = rule(
    implementation = _gnulib_conditional_hdr_impl,
    doc = """\
Conditionally wrap a processed gnulib header with a passthrough fallback.

This rule mirrors upstream gnulib's `gl_CONDITIONAL_HEADER` + Makefile.am
pattern.  It sits downstream of `autoconf_hdr` and decides, based on a
check result, whether the processed wrapper header is needed:

- **Condition truthy** (wrapper needed): the `src` content is output as-is.
- **Condition falsy** (wrapper not needed): the output wraps the processed
  template in a dead `#else` block and activates a `#include_next`
  passthrough so the system header is used instead:

```c
#if 1
#include_next <header.h>
#else
/* processed template (dead code) */
#endif
```

Example:

```python
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//gnulib:conditional_hdr.bzl", "gnulib_conditional_hdr")

autoconf_hdr(
    name = "assert_h_processed",
    out = "lib/assert.processed.h",
    template = "lib/assert.in.h",
    mode = "subst",
    deps = ["@rules_cc_autoconf//gnulib/m4/assert_h"],
)

gnulib_conditional_hdr(
    name = "assert_h",
    src = ":assert_h_processed",
    out = "lib/assert.h",
    condition = "GL_GENERATE_ASSERT_H",
    next_header = "NEXT_ASSERT_H",
    deps = [
        "@rules_cc_autoconf//gnulib/m4/include_next",
        "@rules_cc_autoconf//gnulib/m4/assert_h",
    ],
)
```
""",
    attrs = {
        "condition": attr.string(
            doc = """\
Check result name that determines whether the wrapper is needed.
Looked up in the merged results from `deps`.  When the value is
truthy (non-empty, not `"false"`, not `"0"`), `src` is output as-is.
When falsy, a passthrough is generated using `include_next` and
`next_header`, with the processed template preserved as dead code.""",
            mandatory = True,
        ),
        "deps": attr.label_list(
            doc = """\
Autoconf targets providing check results.  The `condition`,
`include_next`, and `next_header` names are looked up across the
merged results from all deps.""",
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "include_next": attr.string(
            doc = """\
Check result name for the `#include_next` directive value.
Looked up in the merged results from `deps`.""",
            default = "INCLUDE_NEXT",
        ),
        "next_header": attr.string(
            doc = """\
Check result name for the `NEXT_<HEADER>_H` value used in the
passthrough fallback.  Looked up in the merged results from `deps`.""",
            mandatory = True,
        ),
        "out": attr.output(
            doc = "Output header file path.",
            mandatory = True,
        ),
        "src": attr.label(
            doc = "Processed header from `autoconf_hdr` to conditionally wrap.",
            allow_single_file = True,
            mandatory = True,
        ),
        "_runner": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//gnulib/private/conditional_hdr"),
        ),
    },
)

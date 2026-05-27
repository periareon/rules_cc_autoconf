"""Conditional header generation matching gnulib's gl_CONDITIONAL_HEADER pattern."""

# buildifier: disable=bzl-visibility
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
)

# buildifier: disable=bzl-visibility
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load("//gnulib/private:result_lookup.bzl", "find_result_file")

def _gnulib_conditional_hdr_impl(ctx):
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    all_cache = dep_results["cache"]
    all_define = dep_results["define"]
    all_subst = dep_results["subst"]

    condition_file = find_result_file(ctx.attr.condition, all_cache, all_define, all_subst, ctx.label)
    include_next_file = find_result_file(ctx.attr.include_next, all_cache, all_define, all_subst, ctx.label)
    next_header_file = find_result_file(ctx.attr.next_header, all_cache, all_define, all_subst, ctx.label)

    src_file = ctx.file.src

    args = ctx.actions.args()
    args.add("--src", src_file)
    args.add("--output", ctx.outputs.out)
    args.add_all([condition_file], before_each = "--dep", format_each = "{}=%s".format(ctx.attr.condition))
    args.add_all([include_next_file], before_each = "--dep", format_each = "{}=%s".format(ctx.attr.include_next))
    args.add_all([next_header_file], before_each = "--dep", format_each = "{}=%s".format(ctx.attr.next_header))
    args.add("--condition", ctx.attr.condition)
    args.add("--include-next", ctx.attr.include_next)
    args.add("--next-header", ctx.attr.next_header)

    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = [src_file, condition_file, include_next_file, next_header_file],
        outputs = [ctx.outputs.out],
        mnemonic = "GnulibConditionalHdr",
        execution_requirements = {"supports-path-mapping": ""},
    )

    return [DefaultInfo(files = depset([ctx.outputs.out]))]

gnulib_conditional_hdr = rule(
    implementation = _gnulib_conditional_hdr_impl,
    doc = """\
> **Deprecated.** Use [`cc_gnulib_conditional_hdrs`](conditional_hdrs.bzl)
> for new code. The replacement rule writes no file on the falsy branch
> (matching upstream gnulib's `rm -f $@`) rather than synthesizing a
> `#include_next` wrapper, drops the `INCLUDE_NEXT` / `NEXT_*_H`
> requirement, has no MSVC `#include_next` gap, and groups multiple
> gated headers into one `CcInfo` provider.

Conditionally wrap a processed gnulib header with a passthrough fallback.

This rule mirrors upstream gnulib's [`gl_CONDITIONAL_HEADER`](https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/gnulib-common.m4#L1505-L1533)
+ Makefile.am pattern.  It sits downstream of `autoconf_hdr` and decides,
based on a check result, whether the processed wrapper header is needed:

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

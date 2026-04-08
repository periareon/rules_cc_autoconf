"""Custom rule for AC_C_INLINE keyword detection.

Implements the GNU autoconf AC_C_INLINE fallback chain:
  1. inline      — C99/C11 keyword (no #define needed if it works)
  2. __inline__  — GCC/Clang extension
  3. __inline    — MSVC extension
  4. (none)      — #define inline to empty

Wrapped in #ifndef __cplusplus because C++ always supports inline.

This cannot be expressed with the core `autoconf` rule because the fallback
chain requires multiple compile checks that resolve to a single define, and
the core rule rejects duplicate defines for the same name.

The individual keyword compile checks are standard `autoconf` targets listed
in `deps`. The `keywords` dict maps each keyword to the cache variable name
used as the key lookup into the deps' results. The resolver picks the first
successful keyword.
"""

load("//autoconf:cc_autoconf_info.bzl", "CcAutoconfInfo")
load("//autoconf/private:autoconf_config.bzl", "collect_deps", "collect_transitive_results")

def _ac_c_inline_impl(ctx):
    """Implementation of the AC_C_INLINE rule."""

    # Collect all cache results from deps.
    deps = collect_deps(ctx.attr.deps)
    dep_results = collect_transitive_results(deps.to_list())
    all_cache = dep_results["cache"]

    # Build resolver arguments from the checks dict (insertion-ordered).
    resolver_args = ctx.actions.args()
    resolver_args.add("--define", "inline")

    input_files = []
    for keyword, cache_var in ctx.attr.keywords.items():
        if cache_var not in all_cache:
            fail("Cache variable '{}' (for keyword '{}') not found in deps. Available: {}".format(
                cache_var,
                keyword,
                sorted(all_cache.keys()),
            ))
        result_file = all_cache[cache_var]
        resolver_args.add("--check", "{}={}".format(keyword, result_file.path))
        input_files.append(result_file)

    # Run the resolver.
    resolved_result = ctx.actions.declare_file(
        "{}/inline.result.cache.json".format(ctx.label.name),
    )
    resolver_args.add("--output", resolved_result)

    ctx.actions.run(
        executable = ctx.executable._resolver,
        arguments = [resolver_args],
        inputs = input_files,
        outputs = [resolved_result],
        mnemonic = "CcAutoconfInlineResolve",
        progress_message = "CcAutoconfInlineResolve %{label}",
    )

    return [
        CcAutoconfInfo(
            owner = ctx.label,
            cache_results = {"inline": resolved_result},
            define_results = {"inline": resolved_result},
            unquoted_defines = ["inline"],
        ),
        DefaultInfo(files = depset([resolved_result])),
        OutputGroupInfo(
            autoconf_results = depset([resolved_result]),
        ),
    ]

ac_c_inline = rule(
    implementation = _ac_c_inline_impl,
    doc = """\
Detect the C inline keyword variant supported by the compiler.

Implements the GNU autoconf AC_C_INLINE fallback chain. Tries the bare
`inline` keyword first (C99/C11), then `__inline__` (GCC/Clang), then
`__inline` (MSVC). If none compile, defines `inline` to empty.

The `deps` attribute lists standard `autoconf` targets that run the individual
keyword compile checks. The `keywords` attribute maps each keyword to the cache
variable name used as the lookup key in the deps' results.

Returns a CcAutoconfInfo provider with the `inline` define result,
compatible with the standard `autoconf` rule for use as a dependency.
""",
    attrs = {
        "deps": attr.label_list(
            doc = "autoconf targets providing the keyword compile check results.",
            providers = [CcAutoconfInfo],
        ),
        "keywords": attr.string_dict(
            doc = "Ordered mapping of keyword -> cache variable name. Processed in insertion order as the fallback chain.",
            mandatory = True,
        ),
        "_resolver": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/macros/AC_C_INLINE:inline_resolver"),
        ),
    },
    provides = [CcAutoconfInfo],
)

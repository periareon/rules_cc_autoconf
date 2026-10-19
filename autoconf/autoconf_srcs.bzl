"""# autoconf_srcs"""

load("@bazel_features//:features.bzl", "bazel_features")
load("@bazel_skylib//lib:paths.bzl", "paths")
load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_defaults",
    "collect_deps",
    "collect_transitive_results",
    "merge_defaults",
    "merge_results",
)
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars")
load("//autoconf/private:providers.bzl", "CcAutoconfDefaultsInfo", "CcAutoconfInfo")

def _package_relative_name(ctx, file):
    """Returns the package relative path to the file.

    Fails if the file is not in the package of `ctx`.
    """
    pkg = ctx.label.package
    short_path = paths.relativize(file.path, ctx.label.workspace_root)

    if not short_path.startswith(pkg):
        fail("`{}` is not in the same package as `{}`. This is required.".format(
            file.short_path,
            ctx.label,
        ))

    # Return only the part after the package path
    if pkg:
        # pkg is something like "autoconf/tests/srcs"
        # short_path is something like "autoconf/tests/srcs/bad/bad.c"
        # We want to return "bad/bad.c"
        return short_path[len(pkg) + 1:]  # +1 to skip the '/'

    # Package is root, return the full short_path
    return short_path

def _add_ac_suffix(package_relative_name):
    """Insert `.ac.` before the final suffix of the file name.

    Examples:
        "foo.cc"        -> "foo.ac.cc"
        "dir/foo.cc"    -> "dir/foo.ac.cc"
        "foo"           -> "foo.ac"
        "dir/foo"       -> "dir/foo.ac"
    """
    dirname = ""
    basename = package_relative_name
    if "/" in package_relative_name:
        dirname, basename = package_relative_name.rsplit("/", 1)

    if "." in basename:
        stem, ext = basename.rsplit(".", 1)
        new_basename = stem + ".ac." + ext
    else:
        new_basename = basename + ".ac"

    if dirname:
        return dirname + "/" + new_basename
    return new_basename

def _output_name(ctx, file, naming_mode):
    """Compute the output path for a given input file and naming mode."""
    rel = _package_relative_name(ctx, file)

    if naming_mode == "package_relative":
        return rel
    if naming_mode == "ac_suffix":
        return _add_ac_suffix(rel)
    if naming_mode == "per_target":
        return ctx.label.name + "/" + rel

    fail("Unknown autoconf_srcs naming mode: {}".format(naming_mode))

def _lookup_var(var, all_cache, all_define, all_subst, src_label, rule_label):
    """Look up a single variable name across cache/define/subst buckets.

    A name may legitimately appear in more than one bucket (e.g. an
    `AC_DEFINE` followed by an `AC_SUBST` of the same identifier). When the
    matches all refer to the *same underlying result file* the lookup
    succeeds; only genuinely distinct matches trigger the ambiguity error.

    Buckets are not auto-resolved by priority: when an ambiguity occurs the
    user is asked to disambiguate by using the canonical `ac_cv_*` cache
    name (which is unique per check) instead of the shorter define / subst
    name.

    Returns:
        The matched result `File`.
    """
    candidates = []
    if var in all_cache:
        candidates.append(("cache", all_cache[var]))
    if var in all_define:
        candidates.append(("define", all_define[var]))
    if var in all_subst:
        candidates.append(("subst", all_subst[var]))

    if not candidates:
        all_available = {
            "cache": sorted(all_cache.keys()),
            "define": sorted(all_define.keys()),
            "subst": sorted(all_subst.keys()),
        }
        fail("Source `{}` requires `{}` which is not provided by any deps of `{}`. Available options: {}".format(
            src_label,
            var,
            rule_label,
            json.encode_indent(all_available, indent = " " * 4),
        ))

    distinct_paths = {}
    for bucket, f in candidates:
        distinct_paths[f.path] = (bucket, f)

    if len(distinct_paths) != 1:
        fail(("Source `{}` requires `{}` but it resolves to distinct results " +
              "across deps of `{}`.\nMatches: {}\n" +
              "Disambiguate by referencing the canonical `ac_cv_*` cache " +
              "name of the specific check you want, rather than the shorter " +
              "define / subst name.").format(
            src_label,
            var,
            rule_label,
            [(bucket, f.path) for (bucket, f) in candidates],
        ))

    return distinct_paths.values()[0][1]

def _repository_relative(file):
    """The file's path within its repository.

    That is what a quoted include resolves against the repository's own include
    roots (`-iquote external/<repo>` and its output-tree twin), in the main
    repository and in any other.  It is the `short_path` without the
    `../<repo>/` of an external file, and it holds for generated files and under
    path mapping, where `path` would not.

    Args:
        file: The `File` to name.

    Returns:
        The repository-relative path, as a string.
    """
    short = file.short_path
    if short.startswith("../"):
        return short.split("/", 2)[2]
    return short

def _arg_map_src(value):
    in_file, condition, out_file = value
    return [
        "--src",
        "{},{},{}".format(in_file.path, condition, out_file.path),
        "--include-as",
        _repository_relative(in_file),
    ]

def _autoconf_srcs_impl(ctx):
    """Implementation of the autoconf_srcs rule."""

    # Collect transitive results from deps (same approach as autoconf_hdr)
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    merged = merge_results(dep_results, merge_defaults(collect_defaults(ctx.attr.default_deps, dep_infos).to_list()))
    all_cache = merged.cache
    all_define = merged.define
    all_subst = merged.subst

    outputs = []
    originals = []

    for src, condition in ctx.attr.srcs.items():
        files = src.files.to_list()
        if len(files) != 1:
            fail("Label {} in srcs must produce exactly one file, got {}".format(
                src,
                len(files),
            ))

        in_file = files[0]
        out_name = _output_name(ctx, in_file, ctx.attr.naming)
        if ctx.attr.wrap == "include" and ctx.attr.naming != "ac_suffix":
            # The wrapper's `#include "<path>"` is searched for in the wrapper's own
            # directory first, so a wrapper named like a root-level source
            # (`foo.c` including "foo.c") would include itself: the wrapper's
            # name always carries the `.ac` infix.
            out_name = _add_ac_suffix(out_name)
        out = ctx.actions.declare_file(out_name)

        # Extract all variable names from the (possibly compound) condition.
        var_names = extract_condition_vars(condition)

        args = ctx.actions.args()
        dep_files = []

        for var in var_names:
            results_file = _lookup_var(var, all_cache, all_define, all_subst, src, ctx.label)
            args.add_all([results_file], before_each = "--dep", format_each = "{}=%s".format(var))
            dep_files.append(results_file)

        # Use comma separator: {in},{CONDITION},{out}
        args.add_all([(in_file, condition, out)], map_each = _arg_map_src)
        if ctx.attr.wrap == "include":
            args.add("--include-original")

        ctx.actions.run(
            toolchain = None,
            executable = ctx.executable._runner,
            arguments = [args],
            inputs = [in_file] + dep_files,
            outputs = [out],
            mnemonic = "CcAutoconfSrc",
            # TODO: https://github.com/periareon/rules_cc_autoconf/issues/148
            execution_requirements = {"supports-path-mapping": ""} if bazel_features.rules.write_action_has_execution_requirements else {},
        )

        outputs.append(out)
        originals.append(in_file)

    providers = [DefaultInfo(files = depset(outputs))]
    if ctx.attr.wrap == "include":
        # The wrappers `#include` the originals, so the originals must reach
        # the compiler: a consumer that lists this target in `deps` as well as
        # `srcs` gets them as headers of its compilation context.
        providers.append(CcInfo(compilation_context = cc_common.create_compilation_context(
            headers = depset(originals),
        )))
    return providers

autoconf_srcs = rule(
    implementation = _autoconf_srcs_impl,
    doc = """\
Generate wrapper sources that are conditionally enabled by autoconf results.

Typical use case:

```python
load("@rules_cc_autoconf//autoconf:autoconf_srcs.bzl", "autoconf_srcs")
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")
load("@rules_cc//cc:cc_library.bzl", "cc_library")

autoconf(
    name = "config",
    out = "config.h",
    checks = [
        # Feature is present on this platform/toolchain.
        checks.AC_CHECK_HEADER("foo.h", define = "HAVE_FOO"),
    ],
)

autoconf_srcs(
    name = "foo_srcs",
    deps = [":config"],
    srcs = {
        "uses_foo.c": "HAVE_FOO",
    },
)

cc_library(
    name = "foo",
    srcs = [":foo_srcs"],
    hdrs = [":config.h"],
)
```

In this setup `autoconf_srcs` reads the results from `:config` (or multiple targets via `deps`) and generates a
wrapped copy of `uses_foo.c` whose contents are effectively:

```cc
#define HAVE_FOO 1      // when the check for HAVE_FOO succeeds
/* or: #undef HAVE_FOO  // when the check fails */
#ifdef HAVE_FOO
/* original uses_foo.c contents ... */
#endif
```

This is useful when you have platform-specific or optional sources that should
only be compiled when a particular autoconf check passes, without having to
manually maintain `#ifdef` guards in every source file.
""",
    attrs = {
        "default_deps": attr.label_list(
            doc = """\
`autoconf_defaults` targets whose values stand where no result of the name comes through `deps`
(a result overrides a default silently; two defaults of one name must agree).  The defaults the
`deps` carry are read as well.
""",
            providers = [CcAutoconfDefaultsInfo],
        ),
        "defaults": attr.bool(
            doc = """\
Deprecated and ignored: defaults come through `deps` and `default_deps`; the autoconf toolchain
no longer carries any.  Accepted so existing BUILD files keep loading.""",
            default = False,
        ),
        "deps": attr.label_list(
            doc = "List of `autoconf` targets which provide defines. Results from all deps will be merged together, and duplicate define names will produce an error.",
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "naming": attr.string(
            doc = """\
How to name generated sources: `package_relative` keeps the original package-relative path, `ac_suffix`
inserts `.ac.` before the final suffix (e.g. `foo.cc -> foo.ac.cc`), and `per_target` prefixes outputs
with `{ctx.label.name}` to namespace them per rule.  Under `wrap = "include"` the output always carries the
`.ac` infix as well (`foo.c -> foo.ac.c`, `per_target`: `{ctx.label.name}/foo.ac.c`): a wrapper named like
a root-level source would resolve its own `#include "foo.c"` to itself.
""",
            values = [
                "package_relative",
                "ac_suffix",
                "per_target",
            ],
            default = "package_relative",
        ),
        "srcs": attr.label_keyed_string_dict(
            doc = "A mapping of source file to define required to compile the file.",
            allow_files = True,
            mandatory = True,
        ),
        "wrap": attr.string(
            doc = "How a generated file stands for its source.  `copy` (the default) carries a copy of the " +
                  "source's contents.  `include` carries only `#include \"<path of the source>\"`, so the " +
                  "source's own quoted includes (`#include \"sibling.h\"`) still resolve in its directory and " +
                  "`__FILE__` and diagnostics name the source; the sources must then reach the compiler, which " +
                  "they do when the consumer lists this target in `deps` as well as `srcs` (the target " +
                  "provides them as headers of a `CcInfo`).",
            default = "copy",
            values = ["copy", "include"],
        ),
        "_runner": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/src_gen"),
        ),
    },
    fragments = [],
    toolchains = use_cc_toolchain() + [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
)

"""# autoconf_srcs"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "filter_defaults",
    "get_autoconf_toolchain_defaults",
    "get_autoconf_toolchain_defaults_by_label",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _package_relative_name(ctx, file):
    """Returns the package relative path to the file.

    Fails if the file is not in the package of `ctx`.
    """
    pkg = ctx.label.package
    short_path = file.short_path
    if short_path.startswith("../"):
        short_path = short_path[3:]

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

def _autoconf_srcs_impl(ctx):
    """Implementation of the autoconf_srcs rule."""

    # Validate that defaults_include and defaults_exclude are mutually exclusive
    if ctx.attr.defaults_include and ctx.attr.defaults_exclude:
        fail("defaults_include and defaults_exclude are mutually exclusive")

    # Get toolchain defaults based on the defaults attribute
    defaults = struct(cache = {}, define = {}, subst = {})
    if ctx.attr.defaults:
        # Only use label-based filtering if include/exclude is specified
        if ctx.attr.defaults_include or ctx.attr.defaults_exclude:
            # Filtering needed - rebuild filtered list from labels
            defaults_by_label = get_autoconf_toolchain_defaults_by_label(ctx)
            if defaults_by_label:
                include_labels = [dep.label for dep in ctx.attr.defaults_include] if ctx.attr.defaults_include else []
                exclude_labels = [dep.label for dep in ctx.attr.defaults_exclude] if ctx.attr.defaults_exclude else []
                defaults = filter_defaults(defaults_by_label, include_labels, exclude_labels)
        else:
            # No filtering - use already-flattened collection directly from toolchain
            defaults = get_autoconf_toolchain_defaults(ctx)

    # Collect transitive results from deps, keeping groups separate
    cache_results = {}
    define_results = {}
    subst_results = {}

    for dep in ctx.attr.deps:
        dep_info = dep[CcAutoconfInfo]

        # Check for duplicates within each group
        cache_total = len(cache_results)
        cache_new = len(dep_info.cache_results)
        cache_updated = cache_results | dep_info.cache_results
        if cache_total + cache_new != len(cache_updated):
            fail("A duplicate cache variable was detected in dependencies of `{}`: {}".format(
                ctx.label,
                [name for name in dep_info.cache_results.keys() if name in cache_results],
            ))

        define_total = len(define_results)
        define_new = len(dep_info.define_results)
        define_updated = define_results | dep_info.define_results
        if define_total + define_new != len(define_updated):
            fail("A duplicate define was detected in dependencies of `{}`: {}".format(
                ctx.label,
                [name for name in dep_info.define_results.keys() if name in define_results],
            ))

        subst_total = len(subst_results)
        subst_new = len(dep_info.subst_results)
        subst_updated = subst_results | dep_info.subst_results
        if subst_total + subst_new != len(subst_updated):
            fail("A duplicate subst was detected in dependencies of `{}`: {}".format(
                ctx.label,
                [name for name in dep_info.subst_results.keys() if name in subst_results],
            ))

        cache_results = cache_updated
        define_results = define_updated
        subst_results = subst_updated

    # Merge defaults with actual results - results override defaults
    # Use | operator: defaults first, then results override (target prioritizes itself)
    # defaults.cache | cache_results means cache_results overrides defaults.cache ✅
    all_cache = defaults.cache | cache_results
    all_define = defaults.define | define_results
    all_subst = defaults.subst | subst_results

    outputs = []

    for src, define in ctx.attr.srcs.items():
        files = src.files.to_list()
        if len(files) != 1:
            fail("Label {} in srcs must produce exactly one file, got {}".format(
                src,
                len(files),
            ))

        in_file = files[0]
        out_name = _output_name(ctx, in_file, ctx.attr.naming)
        out = ctx.actions.declare_file(out_name)

        # Namespace-agnostic lookup (cache/define/subst) with strict ambiguity detection.
        candidates = []
        if define in all_cache:
            candidates.append(("cache", all_cache[define]))
        if define in all_define:
            candidates.append(("define", all_define[define]))
        if define in all_subst:
            candidates.append(("subst", all_subst[define]))

        if not candidates:
            all_available = {
                "cache": sorted(all_cache.keys()),
                "define": sorted(all_define.keys()),
                "subst": sorted(all_subst.keys()),
            }
            fail("Source `{}` requires `{}` which is not provided by any deps of `{}`. Available options: {}".format(
                src,
                define,
                ctx.label,
                all_available,
            ))

        # Dedupe candidates by underlying result file. If multiple distinct files
        # match the same reference, the reference is ambiguous and must fail.
        distinct_paths = {}
        for bucket, f in candidates:
            distinct_paths[f.path] = (bucket, f)

        if len(distinct_paths) != 1:
            fail("Source `{}` requires `{}` but it is ambiguous across deps of `{}`.\nMatches: {}".format(
                src,
                define,
                ctx.label,
                [(bucket, f.path) for (bucket, f) in candidates],
            ))

        (_, results_file) = distinct_paths.values()[0]
        args = ctx.actions.args()

        # Explicit mapping of lookup name -> result file, similar to checker --dep=name=file.
        args.add("--dep", "{}={}".format(define, results_file.path))
        args.add("--src", "{}={}={}".format(in_file.path, define, out.path))

        ctx.actions.run(
            executable = ctx.executable._runner,
            arguments = [args],
            inputs = [in_file, results_file],
            outputs = [out],
            mnemonic = "CcAutoconfSrc",
        )

        outputs.append(out)

    return [DefaultInfo(files = depset(outputs))]

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
        "defaults": attr.bool(
            doc = """Whether to include toolchain defaults.

            When False (the default), no toolchain defaults are included and only
            the explicit deps provide check results. When True, defaults from the
            autoconf toolchain are included, subject to filtering by defaults_include
            or defaults_exclude.""",
            default = False,
        ),
        "defaults_exclude": attr.label_list(
            doc = """Labels to exclude from toolchain defaults.

            Only effective when defaults=True. If specified, defaults from these
            labels are excluded. Labels not found in the toolchain are silently
            ignored. Mutually exclusive with defaults_include.""",
            providers = [CcAutoconfInfo],
        ),
        "defaults_include": attr.label_list(
            doc = """Labels to include from toolchain defaults.

            Only effective when defaults=True. If specified, only defaults from
            these labels are included. An error is raised if a specified label
            is not found in the toolchain. Mutually exclusive with defaults_exclude.""",
            providers = [CcAutoconfInfo],
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
with `{ctx.label.name}` to namespace them per rule.
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

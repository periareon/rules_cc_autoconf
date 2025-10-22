"""# autoconf_srcs"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
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
        # pkg is something like "autoconf/private/tests/srcs"
        # short_path is something like "autoconf/private/tests/srcs/bad/bad.c"
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

    transitive_checks = {}
    for dep in ctx.attr.deps:
        dep_info = dep[CcAutoconfInfo]
        total = len(transitive_checks)
        new = len(dep_info.results)
        updated = transitive_checks | dep_info.results
        if total + new != len(updated):
            fail("A duplicate check was detected in dependencies of `{}`: {}".format(
                ctx.label,
                [define_name for define_name in dep_info.results.keys() if define_name in transitive_checks],
            ))

        transitive_checks = updated

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

        if define not in transitive_checks:
            fail("Source `{}` requires `{}` which is not provided by any deps of `{}`. Options are: `{}`".format(
                src,
                define,
                ctx.label,
                sorted(transitive_checks.keys()),
            ))

        results_file = transitive_checks[define]
        args = ctx.actions.args()
        args.add("--results", results_file)
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
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")
load("@rules_cc//cc:cc_library.bzl", "cc_library")

autoconf(
    name = "config",
    out = "config.h",
    checks = [
        # Feature is present on this platform/toolchain.
        macros.AC_CHECK_HEADER("foo.h", define = "HAVE_FOO"),
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
    toolchains = use_cc_toolchain(),
)

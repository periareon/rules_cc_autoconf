"""# autoconf"""

load(
    "//autoconf/private:autoconf_library.bzl",
    "to_build_settings_dict",
    _autoconf_rule = "autoconf",
)

def autoconf(
        *,
        name,
        build_settings = None,
        cc_deps = None,
        checks = None,
        conlyopts = None,
        copts = None,
        cxxopts = None,
        default_deps = None,
        deps = None,
        **kwargs):
    """Run autoconf-like checks and produce results.

    This rule resolves the `autoconf_toolchain` (when registered) to skip redundant
    checker actions. If a check's cache variable name already has a result in the
    toolchain or in transitive `deps`, the existing result file is reused.

    Use `autoconf_cache` instead for targets that feed into `autoconf_toolchain`
    to avoid a dependency cycle.

    Example:

    ```python
    load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
    load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

    autoconf(
        name = "config",
        checks = [
            checks.AC_CHECK_HEADER("stdio.h"),
            checks.AC_CHECK_HEADER("stdlib.h"),
            checks.AC_CHECK_FUNC("printf"),
        ],
    )
    ```

    The results can then be used by `autoconf_hdr` or `autoconf_srcs` to generate
    headers or wrapped source files.

    Args:
        name: A unique name for this target.
        build_settings: List of `checks.AC_BUILD_SETTING(...)` entries
            associating Bazel build setting targets (any rule providing
            `BuildSettingInfo`) with autoconf-style defines and substitutions.
        cc_deps: Libraries the checks compile against and link, as configure's
            CPPFLAGS and LIBS carry a project's dependencies: every check sees
            their include directories and defines, and every link probe links
            their archives after its own object. Their flags are part of each
            check's identity, so a check never shares a result between targets
            with different `cc_deps`. Prefer `cc_deps = ["@gmp//:gmp"]` with a
            real `AC_CHECK_LIB` over a static `AC_DEFINE`.
        checks: List of JSON-encoded checks from `checks`
            (e.g., `checks.AC_CHECK_HEADER('stdio.h')`).
        conlyopts: Flags for the C compiles of this target's checks only, after
            `copts` (as on a `cc_library`). Not transitive: flags for every
            check of a build, the gnulib ports included, are `--copt`.
        copts: Flags for every compile of this target's checks, after the
            toolchain's and `--copt` / `--cxxopt` and before a check's own
            `copts` (the order of a `cc_library`'s), so they win over the
            command line where the last flag counts. Not transitive.
        cxxopts: Flags for the C++ compiles of this target's checks only, after
            `copts` (as on a `cc_library`). Not transitive.
        default_deps: `autoconf_defaults` targets. Their values stand where no
            result of the name comes through `deps`; a result overrides a
            default silently, and two defaults of one name must agree. The
            defaults a `deps` entry carries are read as well, and dependents of
            this target read all of them in turn. This is gnulib's
            `*_H_DEFAULTS` pattern (`HAVE_X=1`, `REPLACE_X=0` before the probes
            may reassign them): list a header's `:defaults` here where the m4
            `AC_REQUIRE`s `gl_<HEADER>_H_DEFAULTS`.
        deps: Additional `autoconf`, `autoconf_cache`, or `autoconf_package_info`
            dependencies.
        **kwargs: Standard Bazel attributes (e.g. `visibility`, `tags`).
    """

    # This is a macro rather than a direct rule re-export because the
    # underlying `build_settings` attribute has to be a
    # `label_keyed_string_dict` -- the only pre-Bazel-9 attr type that
    # carries (Label, str) pairs natively. Once Bazel 9 is the minimum
    # supported version, switch the rule attribute to
    # `string_keyed_label_dict`, have `AC_BUILD_SETTING` return a one-key
    # dict, and delete this macro -- the rule can then be re-exported
    # directly.
    _autoconf_rule(
        name = name,
        build_settings = to_build_settings_dict(build_settings),
        cc_deps = cc_deps or [],
        checks = checks or [],
        conlyopts = conlyopts or [],
        copts = copts or [],
        cxxopts = cxxopts or [],
        default_deps = default_deps or [],
        deps = deps or [],
        **kwargs
    )

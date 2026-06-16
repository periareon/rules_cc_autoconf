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
        checks = None,
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
        checks: List of JSON-encoded checks from `checks`
            (e.g., `checks.AC_CHECK_HEADER('stdio.h')`).
        deps: Additional `autoconf`, `autoconf_cache`, or `package_info`
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
        checks = checks or [],
        deps = deps or [],
        **kwargs
    )

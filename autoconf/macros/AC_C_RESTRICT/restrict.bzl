"""Custom rule for AC_C_RESTRICT keyword detection.

Implements the GNU autoconf AC_C_RESTRICT fallback chain:
  1. restrict      — C99 keyword (no #define needed if it works)
  2. __restrict__  — GCC/Clang extension
  3. __restrict    — MSVC extension
  4. (none)        — #define restrict to empty

This cannot be expressed with the core `autoconf` rule because the fallback
chain requires multiple compile checks that resolve to a single define, and
the core rule rejects duplicate defines for the same name.

The rule uses the standard checker binary for the individual compile checks
and a dedicated resolver tool to combine the results.
"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "create_config_dict",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "write_config_json",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

# Test code templates for each keyword variant.
# Each is a minimal C program that uses the keyword in a function signature.
_RESTRICT_CODE = """\
int test(int *restrict p) { return *p; }
int main(void) { return 0; }
"""

_RESTRICT_DUNDER_CODE = """\
int test(int *__restrict__ p) { return *p; }
int main(void) { return 0; }
"""

_UNDERSCORE_RESTRICT_CODE = """\
int test(int *__restrict p) { return *p; }
int main(void) { return 0; }
"""

# Check definitions: (cache_var_name, code, cli_flag_for_resolver)
_KEYWORD_CHECKS = [
    ("_ac_cv_c_restrict", _RESTRICT_CODE, "--restrict"),
    ("_ac_cv_c___restrict__", _RESTRICT_DUNDER_CODE, "--restrict__"),
    ("_ac_cv_c___restrict", _UNDERSCORE_RESTRICT_CODE, "--_restrict"),
]

def _ac_c_restrict_impl(ctx):
    """Implementation of the AC_C_RESTRICT rule."""

    # Set up the CC toolchain (same as the core autoconf rule).
    toolchain_info = get_cc_toolchain_info(ctx)
    config_json = write_config_json(ctx, create_config_dict(
        toolchain_info = toolchain_info,
    ))
    env = get_environment_variables(ctx, toolchain_info)

    # Phase 1: Run the standard checker for each keyword variant.
    check_result_files = []
    resolver_args = ctx.actions.args()

    for cache_name, code, resolver_flag in _KEYWORD_CHECKS:
        # Write the check specification JSON.
        check_spec = {
            "code": code,
            "language": "c",
            "name": cache_name,
            "type": "compile",
        }
        check_json = ctx.actions.declare_file(
            "{}/{}.check.json".format(ctx.label.name, cache_name),
        )
        ctx.actions.write(
            output = check_json,
            content = json.encode_indent(check_spec, indent = "    ") + "\n",
        )

        # Declare the result file.
        result_file = ctx.actions.declare_file(
            "{}/{}.result.cache.json".format(ctx.label.name, cache_name),
        )
        check_result_files.append(result_file)

        # Run the checker (same invocation pattern as the core autoconf rule).
        args = ctx.actions.args()
        args.use_param_file("@%s", use_always = True)
        args.set_param_file_format("multiline")
        args.add("--config", config_json)
        args.add("--check", check_json)
        args.add("--results", result_file)

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = depset([config_json, check_json]),
            outputs = [result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + cache_name,
            env = env | ctx.configuration.default_shell_env,
            tools = toolchain_info.cc_toolchain.all_files,
        )

        # Accumulate resolver arguments.
        resolver_args.add(resolver_flag, result_file)

    # Phase 2: Run the resolver to combine results into a single define.
    restrict_result = ctx.actions.declare_file(
        "{}/restrict.result.cache.json".format(ctx.label.name),
    )
    resolver_args.add("--output", restrict_result)

    ctx.actions.run(
        executable = ctx.executable._resolver,
        arguments = [resolver_args],
        inputs = depset(check_result_files),
        outputs = [restrict_result],
        mnemonic = "CcAutoconfRestrictResolve",
        progress_message = "CcAutoconfRestrictResolve %{label}",
    )

    all_results = check_result_files + [restrict_result]

    # Return CcAutoconfInfo so this rule is a drop-in for any autoconf dep.
    return [
        CcAutoconfInfo(
            owner = ctx.label,
            deps = depset(),
            cache_results = {"restrict": restrict_result},
            define_results = {"restrict": restrict_result},
            subst_results = {},
        ),
        DefaultInfo(files = depset([restrict_result])),
        OutputGroupInfo(
            autoconf_results = depset(all_results),
        ),
    ]

ac_c_restrict = rule(
    implementation = _ac_c_restrict_impl,
    doc = """\
Detect the C restrict keyword variant supported by the compiler.

Implements the GNU autoconf AC_C_RESTRICT fallback chain. Tries the bare
`restrict` keyword first (C99), then `__restrict__` (GCC/Clang), then
`__restrict` (MSVC). If none compile, defines `restrict` to empty.

Returns a CcAutoconfInfo provider with the `restrict` define result,
compatible with the standard `autoconf` rule for use as a dependency.
""",
    attrs = {
        "_checker": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/checker:checker_bin"),
        ),
        "_resolver": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/macros/AC_C_RESTRICT:restrict_resolver"),
        ),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
    provides = [CcAutoconfInfo],
)

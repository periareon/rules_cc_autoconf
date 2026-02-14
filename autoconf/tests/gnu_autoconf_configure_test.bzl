"""gnu_autoconf_configure_test"""

def _rlocationpath(file, workspace_name):
    if file.short_path.startswith("../"):
        return file.short_path[len("../"):]

    return "{}/{}".format(workspace_name, file.short_path)

def _gnu_autoconf_configure_test_impl(ctx):
    test_exe = ctx.executable._tester
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, test_exe.extension).strip("."))
    ctx.actions.symlink(
        target_file = test_exe,
        output = executable,
        is_executable = True,
    )

    data_files = [
        executable,
        ctx.file.configure_ac,
        ctx.file.config_h_in,
        ctx.file.golden_config_h,
    ] + ctx.files.m4_files + ctx.files.aux_files

    env = {
        "FORCE_UNSAFE_CONFIGURE": "1",
        "TEST_CONFIGURE_AC": _rlocationpath(ctx.file.configure_ac, ctx.workspace_name),
        "TEST_CONFIG_H_IN": _rlocationpath(ctx.file.config_h_in, ctx.workspace_name),
        "TEST_GOLDEN_CONFIG_H": _rlocationpath(ctx.file.golden_config_h, ctx.workspace_name),
        "TEST_M4_FILES": " ".join([
            _rlocationpath(file, ctx.workspace_name)
            for file in ctx.files.m4_files
        ]),
    }

    if ctx.file.subst_h_in:
        env.update({
            "TEST_GOLDEN_SUBST_H": _rlocationpath(ctx.file.golden_subst_h, ctx.workspace_name),
            "TEST_SUBST_H_IN": _rlocationpath(ctx.file.subst_h_in, ctx.workspace_name),
        })

        data_files.extend([
            ctx.file.subst_h_in,
            ctx.file.golden_subst_h,
        ])

    if ctx.attr.verify_variables:
        env["VERIFY_VARIABLES"] = "1"

    # Add aux_files to environment if provided
    if ctx.files.aux_files:
        env["TEST_AUX_FILES"] = " ".join([
            _rlocationpath(file, ctx.workspace_name)
            for file in ctx.files.aux_files
        ])

    return [
        DefaultInfo(
            files = depset([executable]),
            runfiles = ctx.attr._tester[DefaultInfo].default_runfiles.merge(ctx.runfiles(data_files)),
            executable = executable,
        ),
        RunEnvironmentInfo(
            environment = env,
            inherited_environment = ["PATH"],
        ),
    ]

_gnu_autoconf_configure_test = rule(
    doc = "Single test that runs GNU autoconf and compares output with golden files.",
    implementation = _gnu_autoconf_configure_test_impl,
    attrs = {
        "aux_files": attr.label_list(
            doc = "Auxiliary files (e.g., config.rpath) to copy to work directory root",
            allow_files = True,
        ),
        "config_h_in": attr.label(
            doc = "Template for AC_DEFINE (#undef patterns)",
            allow_single_file = True,
            mandatory = True,
        ),
        "configure_ac": attr.label(
            doc = "The configure.ac file specific to this m4 module",
            allow_single_file = True,
            mandatory = True,
        ),
        "golden_config_h": attr.label(
            doc = "Expected config.h output",
            allow_single_file = True,
            mandatory = True,
        ),
        "golden_subst_h": attr.label(
            doc = "Expected gnulib_*.h output",
            allow_single_file = True,
        ),
        "m4_files": attr.label_list(
            doc = "ALL m4 files needed to run autoconf (including deps)",
            allow_files = True,
        ),
        "subst_h_in": attr.label(
            doc = "Template for AC_SUBST (@FOO@ patterns)",
            allow_single_file = True,
        ),
        "verify_variables": attr.bool(
            doc = "If True, template files will be required to have all autoconf produced variables.",
        ),
        "_tester": attr.label(
            executable = True,
            cfg = "target",
            default = Label("//autoconf/tests:gnu_autoconf_configure_tester"),
        ),
    },
    test = True,
)

def gnu_autoconf_configure_test(*, name, tags = [], **kwargs):
    _gnu_autoconf_configure_test(
        name = name,
        tags = tags + ["gnu_autoconf_test"],
        **kwargs
    )

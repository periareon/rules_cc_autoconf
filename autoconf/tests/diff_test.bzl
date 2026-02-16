"""diff_test"""

def _rlocationpath(file, workspace_name):
    if file.short_path.startswith("../"):
        return file.short_path[len("../"):]

    return "{}/{}".format(workspace_name, file.short_path)

def _diff_test_impl(ctx):
    test_exe = ctx.executable._tester
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, test_exe.extension).strip("."))
    ctx.actions.symlink(
        target_file = test_exe,
        output = executable,
        is_executable = True,
    )

    return [
        DefaultInfo(
            files = depset([executable]),
            runfiles = ctx.attr._tester[DefaultInfo].default_runfiles.merge(ctx.runfiles([ctx.file.file1, ctx.file.file2, executable])),
            executable = executable,
        ),
        RunEnvironmentInfo(
            environment = {
                "TEST_FILE_1": _rlocationpath(ctx.file.file1, ctx.workspace_name),
                "TEST_FILE_2": _rlocationpath(ctx.file.file2, ctx.workspace_name),
            },
        ),
    ]

diff_test = rule(
    doc = "A replacement for `bazel_skylib` `diff_test` that shows diffs on all platforms.",
    implementation = _diff_test_impl,
    attrs = {
        "file1": attr.label(
            doc = "The left file to test.",
            mandatory = True,
            allow_single_file = True,
        ),
        "file2": attr.label(
            doc = "The right file to test.",
            mandatory = True,
            allow_single_file = True,
        ),
        "_tester": attr.label(
            executable = True,
            cfg = "target",
            default = Label("//autoconf/tests:diff_tester"),
        ),
    },
    test = True,
)

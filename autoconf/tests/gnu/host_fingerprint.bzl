"""Record the host C/C++ toolchain identity as a build input.

`local_config_cc` does not change when Xcode or GCC is upgraded, so results
cached from an older compiler survive the upgrade and failures surface only on
fresh machines.  This repository rule writes the compiler and SDK versions to
`fingerprint.json`; anything that depends on that file is re-run when they
change.  It is best effort and never fails: on a machine where nothing can be
detected the file simply records that.
"""

def _first_line(ctx, argv):
    result = ctx.execute(argv, timeout = 60)
    if result.return_code != 0:
        return ""
    output = (result.stdout or result.stderr or "").strip()
    return output.splitlines()[0] if output else ""

def _host_cc_fingerprint_impl(ctx):
    fingerprint = {
        "arch": ctx.os.arch,
        "cc": "",
        "compiler_version": "",
        "developer_dir": "",
        "os": ctx.os.name,
        "sdk_path": "",
        "sdk_version": "",
    }

    cc = ctx.getenv("CC") or ctx.getenv("BAZEL_CC") or ""
    if not cc:
        for candidate in ["cc", "gcc", "clang", "cl"]:
            path = ctx.which(candidate)
            if path:
                cc = str(path)
                break
    if cc:
        fingerprint["cc"] = cc
        fingerprint["compiler_version"] = _first_line(ctx, [cc, "--version"])
        if not fingerprint["compiler_version"]:
            # cl.exe prints its banner to stderr when run bare.
            fingerprint["compiler_version"] = _first_line(ctx, [cc])

    if ctx.os.name.startswith("mac"):
        xcrun = ctx.which("xcrun")
        if xcrun:
            fingerprint["sdk_version"] = _first_line(ctx, [xcrun, "--show-sdk-version"])
            fingerprint["sdk_path"] = _first_line(ctx, [xcrun, "--show-sdk-path"])
            if fingerprint["sdk_path"]:
                settings = ctx.path(fingerprint["sdk_path"] + "/SDKSettings.json")
                if settings.exists:
                    ctx.watch(settings)
        xcode_select = ctx.which("xcode-select")
        if xcode_select:
            fingerprint["developer_dir"] = _first_line(ctx, [xcode_select, "-p"])
        fingerprint["developer_dir_env"] = ctx.getenv("DEVELOPER_DIR") or ""

    if cc:
        cc_path = ctx.path(cc)
        if cc_path.exists:
            ctx.watch(cc_path)

    ctx.file("fingerprint.json", json.encode_indent(fingerprint, indent = "  ") + "\n")
    ctx.file("BUILD.bazel", """\
exports_files(["fingerprint.json"], visibility = ["//visibility:public"])
""")

host_cc_fingerprint = repository_rule(
    doc = "Writes the host compiler and SDK versions to `fingerprint.json`.",
    implementation = _host_cc_fingerprint_impl,
    configure = True,
    local = True,
    environ = ["CC", "BAZEL_CC", "DEVELOPER_DIR", "SDKROOT"],
)

// A library check (AC_CHECK_LIB) compiles and links in two steps, like the
// other link probes, and the -l<library> under test follows the object.
//
// A fake compiler stands in for the toolchain: its compile step rejects a
// link-only flag, as hermetic-llvm's did in effect (its link flags carry a
// -resource-dir without clang's builtin headers, so a one-step
// `cc <compile flags> <link flags> conftest.c -lcrypt` could not find
// <limits.h>), and its link step insists on that flag and on -l<library>
// after the object.
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_runner.h"
#include "autoconf/private/checker/config.h"
#include "tools/json/json.h"

using rules_cc_autoconf::Check;
using rules_cc_autoconf::CheckResult;
using rules_cc_autoconf::CheckRunner;
using rules_cc_autoconf::Config;

namespace {

const char* kFakeCompiler = R"(#!/bin/sh
# Fake cc: a compile step (-c) must not see the link-only flag; a link step
# must see it and -lfoo after the object.
log="$FAKE_CC_LOG"
echo "$@" >> "$log"
out=""
compile=0
link_flag=0
object_seen=0
library_after_object=0
previous=""
for argument in "$@"; do
    case "$argument" in
        -c) compile=1 ;;
        -Wl,--link-only-flag) link_flag=1 ;;
        *.o) object_seen=1 ;;
        -lfoo) [ "$object_seen" = 1 ] && library_after_object=1 ;;
    esac
    [ "$previous" = "-o" ] && out="$argument"
    previous="$argument"
done
if [ "$compile" = 1 ]; then
    [ "$link_flag" = 1 ] && exit 1
    : > "$out"
    exit 0
fi
[ "$link_flag" = 1 ] || exit 1
[ "$library_after_object" = 1 ] || exit 1
: > "$out"
exit 0
)";

std::filesystem::path temp_dir() {
    const char* env = std::getenv("TEST_TMPDIR");
    std::filesystem::path dir = env ? std::filesystem::path(env)
                                    : std::filesystem::temp_directory_path();
    dir /= "library_check_test";
    std::filesystem::create_directories(dir);
    return dir;
}

int count_lines(const std::filesystem::path& path) {
    std::ifstream in(path);
    int lines = 0;
    std::string line;
    while (std::getline(in, line)) ++lines;
    return lines;
}

}  // namespace

int main() {
#ifdef _WIN32
    std::cout << "library_check_test: skipped on Windows (the fake compiler is "
                 "a shell script)"
              << std::endl;
    return 0;
#else
    const std::filesystem::path dir = temp_dir();
    const std::filesystem::path compiler = dir / "fake-cc";
    const std::filesystem::path log = dir / "invocations.log";
    {
        std::ofstream out(compiler);
        out << kFakeCompiler;
    }
    std::filesystem::permissions(compiler,
                                 std::filesystem::perms::owner_exec |
                                     std::filesystem::perms::owner_read |
                                     std::filesystem::perms::owner_write);
    std::filesystem::remove(log);
    setenv("FAKE_CC_LOG", log.string().c_str(), 1);

    Config config;
    config.c_compiler = compiler.string();
    config.c_flags = {"-DCOMPILE_FLAG"};
    config.c_link_flags = {"-Wl,--link-only-flag"};
    config.compiler_type = "gcc";

    nlohmann::json check_json = {
        {"name", "ac_cv_lib_foo_foo"},
        {"type", "lib"},
        {"library", "foo"},
        {"language", "c"},
        {"define", "HAVE_LIBFOO"},
        {"code", "int main(void) { return 0; }\n"},
    };
    std::optional<Check> check = Check::from_json(&check_json);
    if (!check) {
        std::cerr << "FAILED: the lib check did not parse" << std::endl;
        return 1;
    }

    CheckRunner runner(config);
    runner.set_source_id("library_check_test", dir);
    const CheckResult result = runner.run_check(*check);

    int failures = 0;
    if (!result.success) {
        std::cerr << "FAILED: the library probe answered no; the compile step "
                     "saw the link-only flag, or the link step lacked it or "
                     "-lfoo after the object"
                  << std::endl;
        ++failures;
    }
    const int invocations = count_lines(log);
    if (invocations != 2) {
        std::cerr << "FAILED: expected a compile and a link invocation, got "
                  << invocations << std::endl;
        ++failures;
    }
    if (failures == 0) {
        std::cout << "library_check_test: compile then link, -lfoo after the "
                     "object... PASSED"
                  << std::endl;
    }
    return failures == 0 ? 0 : 1;
#endif
}

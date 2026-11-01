// Re-execute this binary so process checks need no shell or installed tools.
#include "autoconf/private/checker/process.h"

#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <system_error>
#include <vector>

#include "tools/json/json.h"

namespace {

std::string read_file(const std::filesystem::path& path) {
    std::ifstream file(path);
    return {std::istreambuf_iterator<char>(file), {}};
}

int child(int argc, char** argv) {
    if (std::string(argv[2]) == "exit127") return 127;
    if (std::string(argv[2]) == "signal") {
        std::signal(SIGTERM, SIG_DFL);
        std::raise(SIGTERM);
        return 1;
    }
    for (int i = 2; i < argc; ++i) std::cout << "[" << argv[i] << "]\n";
    std::cerr << "child stderr\n";
    return 23;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc >= 3 && std::string(argv[1]) == "--child") {
        return child(argc, argv);
    }

    using rules_cc_autoconf::run_process;
    const auto dir = std::filesystem::path(std::getenv("TEST_TMPDIR"));
    const auto executable = dir / "program with ' spaces";
    std::filesystem::create_symlink(std::filesystem::absolute(argv[0]),
                                    executable);
    const auto stdout_path = dir / "stdout";
    const auto stderr_path = dir / "stderr";
    const std::vector<std::string> args{executable.string(), "--child", "a b",
                                        "", "$HOME;*'\"\\"};
    if (run_process(args, stdout_path, stderr_path) != 23 ||
        read_file(stdout_path) != "[a b]\n[]\n[$HOME;*'\"\\]\n" ||
        read_file(stderr_path) != "child stderr\n") {
        std::cerr << "FAILED: argv, streams or child exit status changed\n";
        return 1;
    }

    try {
        run_process({executable.string(), "--child", "signal"}, {}, {});
        std::cerr << "FAILED: signal termination was reported as an exit\n";
        return 1;
    } catch (const std::system_error& err) {
        if (err.code() != std::errc::interrupted) throw;
    }
    try {
        run_process({executable.string(), "--child", "exit127"}, {}, {});
        std::cerr << "FAILED: ambiguous startup failure was accepted\n";
        return 1;
    } catch (const std::system_error& err) {
        if (err.code() != std::errc::io_error) throw;
    }

    // A missing compiler is an execution error, including inside the integer
    // probes whose out-of-range answers otherwise become false capabilities.
    const auto checker =
        std::filesystem::path(std::getenv("TEST_SRCDIR")) / argv[1];
    const auto config_path = dir / "config.json";
    const auto check_path = dir / "check.json";
    const auto result_path = dir / "result.json";
    nlohmann::json config = {
        {"c_compiler", (dir / "missing-compiler").string()},
        {"cpp_compiler", ""},
        {"c_flags", nlohmann::json::array()},
        {"cpp_flags", nlohmann::json::array()},
        {"c_link_flags", nlohmann::json::array()},
        {"cpp_link_flags", nlohmann::json::array()},
        {"compiler_type", "clang"},
        {"linker", ""},
    };
    std::ofstream(config_path) << config;
    for (const char* type : {"compile", "sizeof", "alignof"}) {
        nlohmann::json json = {
            {"name", "missing_compiler"},
            {"type", type},
            {"language", "c"},
            {"code", "{$sizeof(int)} int a[({lhs} < {rhs}) ? 1 : -1];"},
        };
        std::ofstream(check_path) << json;
        const int status = run_process(
            {checker.string(), "--config", config_path.string(), "--check",
             check_path.string(), "--results", result_path.string()},
            stdout_path, stderr_path);
        if (status != 1 || std::filesystem::exists(result_path) ||
            read_file(stderr_path).find("missing-compiler") ==
                std::string::npos) {
            std::cerr << "FAILED: " << type
                      << " checker hid the missing compiler\n";
            return 1;
        }
    }
    std::cout << "process_test: argv, streams, exit, signal and launch errors "
                 "PASSED\n";
    return 0;
}

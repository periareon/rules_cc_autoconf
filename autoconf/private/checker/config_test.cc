#include "autoconf/private/checker/config.h"

#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

using rules_cc_autoconf::Config;

static int test_count = 0;
static int pass_count = 0;

#define TEST(name)                          \
    std::cout << "  " << #name << "... ";   \
    test_count++;                           \
    if (test_##name()) {                    \
        std::cout << "PASSED" << std::endl; \
        pass_count++;                       \
    } else {                                \
        std::cout << "FAILED" << std::endl; \
    }

static const char* const kConfig = R"({
    "c_compiler": "bazel-out/cfg/bin/external/llvm+/clang",
    "c_flags": ["-O2", "-DFOO=a=b"],
    "c_link_flags": ["-lm"],
    "c_target_flags": ["-Wall"],
    "compiler_type": "clang",
    "cpp_compiler": "clang++",
    "cpp_flags": ["-std=c++17"],
    "cpp_link_flags": ["-lstdc++"],
    "cpp_target_flags": ["-Wextra"],
    "linker": "lld"
})";

static bool test_parses_every_field() {
    std::unique_ptr<Config> config = Config::from_json(kConfig);
    return config->c_compiler == "bazel-out/cfg/bin/external/llvm+/clang" &&
           config->cpp_compiler == "clang++" && config->linker == "lld" &&
           config->compiler_type == "clang" &&
           config->c_flags == std::vector<std::string>{"-O2", "-DFOO=a=b"} &&
           config->cpp_flags == std::vector<std::string>{"-std=c++17"} &&
           config->c_target_flags == std::vector<std::string>{"-Wall"} &&
           config->cpp_target_flags == std::vector<std::string>{"-Wextra"} &&
           config->c_link_flags == std::vector<std::string>{"-lm"} &&
           config->cpp_link_flags == std::vector<std::string>{"-lstdc++"} &&
           config->link_libraries.empty();
}

static bool test_optional_fields_default_empty() {
    std::unique_ptr<Config> config = Config::from_json(
        R"({"c_compiler": "gcc", "c_flags": [], "c_link_flags": [],
            "compiler_type": "gcc", "cpp_compiler": "g++", "cpp_flags": [],
            "cpp_link_flags": [], "linker": "gcc"})");
    return config->c_target_flags.empty() && config->cpp_target_flags.empty() &&
           config->link_libraries.empty();
}

static bool test_missing_field_throws() {
    try {
        Config::from_json(R"({"c_compiler": "gcc"})");
    } catch (const std::runtime_error& err) {
        return std::string(err.what()).find("c_flags") != std::string::npos;
    }
    return false;
}

static bool test_malformed_json_throws() {
    try {
        Config::from_json("not json");
    } catch (const std::runtime_error&) {
        return true;
    }
    return false;
}

int main() {
    std::cout << "Running Config tests..." << std::endl;

    TEST(parses_every_field);
    TEST(optional_fields_default_empty);
    TEST(missing_field_throws);
    TEST(malformed_json_throws);

    std::cout << std::endl
              << pass_count << "/" << test_count << " tests passed"
              << std::endl;
    return pass_count == test_count ? 0 : 1;
}

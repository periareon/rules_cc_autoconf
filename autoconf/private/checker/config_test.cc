#include "autoconf/private/checker/config.h"

#include <iostream>
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

static const std::string kBinDir = "bazel-out/k8-fastbuild/bin";
static const std::string kMappedBinDir = "bazel-out/cfg/bin";

static bool test_maps_paths_and_flags() {
    Config config;
    config.c_compiler = kBinDir + "/external/llvm+/clang";
    config.linker = kBinDir + "/external/llvm+/ld";
    config.c_link_flags = {"-L" + kBinDir + "/a:" + kBinDir + "/b", "-lm"};
    config.link_libraries = {kBinDir + "/liba.a"};
    config.map_bin_dir(kBinDir, kMappedBinDir);
    return config.c_compiler == kMappedBinDir + "/external/llvm+/clang" &&
           config.linker == kMappedBinDir + "/external/llvm+/ld" &&
           config.c_link_flags ==
               std::vector<std::string>{
                   "-L" + kMappedBinDir + "/a:" + kMappedBinDir + "/b",
                   "-lm"} &&
           config.link_libraries ==
               std::vector<std::string>{kMappedBinDir + "/liba.a"};
}

static bool test_leaves_other_paths() {
    Config config;
    config.cpp_compiler = "external/llvm+/bin/clang++";
    config.cpp_flags = {"-Ibazel-out/k8-opt-exec/bin/include", "-Wall"};
    config.map_bin_dir(kBinDir, kMappedBinDir);
    return config.cpp_compiler == "external/llvm+/bin/clang++" &&
           config.cpp_flags ==
               std::vector<std::string>{"-Ibazel-out/k8-opt-exec/bin/include",
                                        "-Wall"};
}

int main() {
    std::cout << "Running Config tests..." << std::endl;

    TEST(maps_paths_and_flags);
    TEST(leaves_other_paths);

    std::cout << std::endl
              << pass_count << "/" << test_count << " tests passed"
              << std::endl;
    return pass_count == test_count ? 0 : 1;
}

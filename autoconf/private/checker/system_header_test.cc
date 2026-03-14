#include "autoconf/private/checker/system_header.h"

#include <cassert>
#include <iostream>
#include <string>

using rules_cc_autoconf::parse_line_markers;

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

static bool test_gcc_line_marker() {
    std::string output = R"(# 1 "conftest.c"
# 1 "<built-in>" 1
# 1 "<command-line>" 1
# 1 "conftest.c"
# 1 "/usr/include/stddef.h" 1 3 4
typedef long unsigned int size_t;
# 2 "conftest.c" 2
)";
    auto result = parse_line_markers(output, "stddef.h");
    if (!result.has_value()) return false;
    return result->string().find("stddef.h") != std::string::npos &&
           result->string().find("/usr/include/") != std::string::npos;
}

static bool test_msvc_line_marker() {
    std::string output =
        "#line 1 \"conftest.c\"\r\n"
        "#line 1 \"C:\\\\Program Files (x86)\\\\Windows Kits\\\\10\\\\include"
        "\\\\10.0.26100.0\\\\ucrt\\\\stddef.h\"\r\n"
        "typedef unsigned long long size_t;\r\n"
        "#line 2 \"conftest.c\"\r\n";
    auto result = parse_line_markers(output, "stddef.h");
    if (!result.has_value()) return false;
    return result->string().find("stddef.h") != std::string::npos;
}

static bool test_header_not_found() {
    std::string output = R"(# 1 "conftest.c"
# 1 "<built-in>" 1
# 1 "conftest.c"
int x;
)";
    auto result = parse_line_markers(output, "unistd.h");
    return !result.has_value();
}

static bool test_skips_conftest_path() {
    std::string output = R"(# 1 "conftest.stddef.h"
# 1 "/usr/include/stddef.h" 1 3 4
typedef long unsigned int size_t;
)";
    auto result = parse_line_markers(output, "stddef.h");
    if (!result.has_value()) return false;
    return result->string().find("/usr/include/") != std::string::npos;
}

static bool test_sys_header_path() {
    std::string output = R"(# 1 "conftest.c"
# 1 "/usr/include/sys/stat.h" 1 3 4
struct stat {};
# 2 "conftest.c" 2
)";
    auto result = parse_line_markers(output, "sys/stat.h");
    if (!result.has_value()) return false;
    return result->string().find("sys/stat.h") != std::string::npos;
}

static bool test_bare_header_name() {
    std::string output = R"(# 1 "conftest.c"
# 1 "stddef.h" 1 3 4
typedef long unsigned int size_t;
)";
    auto result = parse_line_markers(output, "stddef.h");
    if (!result.has_value()) return false;
    return result->string() == "stddef.h";
}

int main() {
    std::cout << "system_header_test:" << std::endl;
    TEST(gcc_line_marker)
    TEST(msvc_line_marker)
    TEST(header_not_found)
    TEST(skips_conftest_path)
    TEST(sys_header_path)
    TEST(bare_header_name)

    std::cout << std::endl
              << pass_count << "/" << test_count << " tests passed."
              << std::endl;
    return pass_count == test_count ? 0 : 1;
}

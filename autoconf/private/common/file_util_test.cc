// Tests for the output stream the checker writes a check's program through:
// binary mode, so the compiler gets the program byte for byte.

#include "autoconf/private/common/file_util.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

using rules_cc_autoconf::open_ofstream;

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

namespace {

std::filesystem::path temp_path(const char* name) {
    const char* dir = std::getenv("TEST_TMPDIR");
    return std::filesystem::path(dir ? dir : ".") / name;
}

std::string read_bytes(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in),
                       std::istreambuf_iterator<char>());
}

bool round_trips(const char* name, const std::string& text) {
    const std::filesystem::path path = temp_path(name);
    {
        std::ofstream out =
            open_ofstream(path, std::ios::out | std::ios::binary);
        if (!out.is_open()) return false;
        out << text;
    }
    const bool same = read_bytes(path) == text;
    std::filesystem::remove(path);
    return same;
}

// A program spelled with CRLF (a BUILD file checked out with autocrlf) must
// reach the compiler with CRLF, not CR CR LF: the splice after `#if \` relies
// on it.
bool test_crlf_kept() {
    return round_trips(
        "crlf.c",
        "#if \\\r\n    (A)\r\n#endif\r\nint main(void) { return 0; }\r\n");
}

bool test_lf_kept() {
    return round_trips(
        "lf.c", "#if \\\n    (A)\n#endif\nint main(void) { return 0; }\n");
}

}  // namespace

int main() {
    std::cout << "open_ofstream binary tests:" << std::endl;
    TEST(crlf_kept);
    TEST(lf_kept);
    std::cout << pass_count << "/" << test_count << " passed" << std::endl;
    return pass_count == test_count ? 0 : 1;
}

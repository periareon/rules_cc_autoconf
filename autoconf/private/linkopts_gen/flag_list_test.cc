// Tests for flatten_flags, the part of linkopts_gen that turns LIBS-style
// substitution values into the linker response file.

#include "autoconf/private/linkopts_gen/flag_list.h"

#include <iostream>
#include <string>
#include <vector>

using rules_cc_autoconf::flatten_flags;

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

using Flags = std::vector<std::string>;

bool expect(const Flags& actual, const Flags& expected) {
    if (actual == expected) return true;
    std::cout << "\n    expected:";
    for (const auto& f : expected) std::cout << " [" << f << "]";
    std::cout << "\n    actual:  ";
    for (const auto& f : actual) std::cout << " [" << f << "]";
    std::cout << "\n  ";
    return false;
}

// One option per element, even when a value holds several.
bool test_splits_on_whitespace() {
    return expect(flatten_flags({"-lpthread", " -lm  -ldl\t"}),
                  {"-lpthread", "-lm", "-ldl"});
}

// Empty values (unprobed or unneeded libraries) contribute nothing.
bool test_skips_empty_values() {
    return expect(flatten_flags({"", "-lrt", "   "}), {"-lrt"});
}

// The same library named by two variables is linked once, first position.
bool test_dedups_repeated_libraries() {
    return expect(flatten_flags({"-lpthread", "-lm -lpthread", "-lm"}),
                  {"-lpthread", "-lm"});
}

// gnulib's INTL_MACOSX_LIBS: the second `-Wl,-framework` must survive, or ld
// is handed a bare `CoreServices` ("file cannot be open()ed ...
// path=CoreServices").
bool test_keeps_repeated_framework_option() {
    return expect(flatten_flags({"-Wl,-framework -Wl,CoreFoundation "
                                 "-Wl,-framework -Wl,CoreServices"}),
                  {"-Wl,-framework", "-Wl,CoreFoundation", "-Wl,-framework",
                   "-Wl,CoreServices"});
}

// The same holds for the compiler-driver spelling, and a framework named
// twice (from two variables) is still linked once.
bool test_dedups_whole_framework_units() {
    return expect(flatten_flags({"-framework CoreFoundation -lintl",
                                 "-framework CoreServices "
                                 "-framework CoreFoundation"}),
                  {"-framework", "CoreFoundation", "-lintl", "-framework",
                   "CoreServices"});
}

// A trailing paired option with no argument is passed through untouched;
// the linker, not the generator, reports it.
bool test_dangling_paired_option_kept() {
    return expect(flatten_flags({"-lm -framework"}), {"-lm", "-framework"});
}

}  // namespace

int main() {
    std::cout << "flag_list_test:" << std::endl;
    TEST(splits_on_whitespace)
    TEST(skips_empty_values)
    TEST(dedups_repeated_libraries)
    TEST(keeps_repeated_framework_option)
    TEST(dedups_whole_framework_units)
    TEST(dangling_paired_option_kept)

    std::cout << std::endl
              << pass_count << "/" << test_count << " tests passed."
              << std::endl;
    return pass_count == test_count ? 0 : 1;
}

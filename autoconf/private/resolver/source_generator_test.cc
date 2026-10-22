// Unit tests for SourceGenerator's placeholder substitution.

#include "autoconf/private/resolver/source_generator.h"

#include <cstdio>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using rules_cc_autoconf::CheckResult;
using rules_cc_autoconf::CheckType;
using rules_cc_autoconf::Mode;
using rules_cc_autoconf::SourceGenerator;

namespace {

std::string render(const std::vector<CheckResult>& substs,
                   const std::string& template_content) {
    SourceGenerator generator({}, {}, substs, Mode::kSubst);
    // Bazel's test temp directory: temp_directory_path() needs TMP / TEMP,
    // which the Windows test sandbox does not pass through.
    const char* tmpdir = std::getenv("TEST_TMPDIR");
    std::filesystem::path out =
        (tmpdir ? std::filesystem::path(tmpdir)
                : std::filesystem::temp_directory_path()) /
        "source_generator_test.h";
    generator.generate_config_header(out, template_content);
    std::stringstream buffer;
    {
        // Closed before the remove: Windows refuses to delete an open file.
        std::ifstream in(out);
        buffer << in.rdbuf();
    }
    std::filesystem::remove(out);
    return buffer.str();
}

CheckResult subst(const std::string& name, const std::string& value) {
    return CheckResult(name, value, true, false, true, CheckType::kGlNextHeader,
                       std::nullopt, name);
}

int failures = 0;

void expect_eq(const std::string& what, const std::string& got,
               const std::string& want) {
    if (got == want) {
        std::cout << "  " << what << "... PASSED" << std::endl;
    } else {
        std::cout << "  " << what << "... FAILED\n--- got ---\n"
                  << got << "\n--- want ---\n"
                  << want << std::endl;
        ++failures;
    }
}

}  // namespace

int run() {
    // A system header inlined for #include_next (MSVC) is a multi-line
    // value: it replaces the include line but not a mention in a comment,
    // whose first `*/` would otherwise end the comment early.
    std::string inlined =
        "\n#ifndef _RCCAC_GL_NEXT_INLINED_STDINT_H\n"
        "/* system stdint.h */\n#endif\n";
    std::vector<CheckResult> substs = {
        subst("NEXT_STDINT_H", inlined),
        subst("INCLUDE_NEXT", "include"),
        subst("GUARD_PREFIX", "GL"),
    };
    expect_eq("multi-line value in a block comment stays a placeholder",
              render(substs, "/* <sys/types.h> after @NEXT_STDINT_H@.  */\n"),
              "/* <sys/types.h> after @NEXT_STDINT_H@.  */\n");
    expect_eq("multi-line value in a line comment stays a placeholder",
              render(substs, "// see @NEXT_STDINT_H@\nint x;\n"),
              "// see @NEXT_STDINT_H@\nint x;\n");
    // (The generator drops the space left before the value's leading newline.)
    expect_eq("multi-line value on the include line is inlined",
              render(substs, "# @INCLUDE_NEXT@ @NEXT_STDINT_H@\n"),
              "# include" + inlined + "\n");
    expect_eq("single-line values are substituted inside comments too",
              render(substs, "#endif /* _@GUARD_PREFIX@_STDINT_H */\n"),
              "#endif /* _GL_STDINT_H */\n");
    expect_eq("a comment closed on the same line does not swallow the rest",
              render(substs, "/* a */ #define X _@GUARD_PREFIX@_X\n"),
              "/* a */ #define X _GL_X\n");
    if (failures != 0) {
        std::cout << failures << " test(s) failed" << std::endl;
        return 1;
    }
    std::cout << "All tests passed" << std::endl;
    return 0;
}

int main() {
    try {
        return run();
    } catch (const std::exception& error) {
        std::cout << "exception: " << error.what() << std::endl;
        return 1;
    }
}

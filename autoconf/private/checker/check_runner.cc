#include "autoconf/private/checker/check_runner.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <system_error>

#include "autoconf/private/checker/debug_logger.h"

namespace rules_cc_autoconf {

CheckRunner::CheckRunner(const Config& config) : config_(config) {}

std::vector<CheckResult> CheckRunner::run_all_checks() {
    results_.clear();

    for (const Check& check : config_.checks) {
        CheckResult result = run_check(check);
        results_.push_back(result);

        std::string status = result.success ? "yes" : "no";
        DebugLogger::log("checking " + check.name() + "... " + status);
    }

    return results_;
}

CheckResult CheckRunner::run_check(const Check& check) {
    DebugLogger::debug("Running check for " + check.define());
    switch (check.type()) {
        case CheckType::kHeader:
            return check_header(check);
        case CheckType::kFunction:
            return check_function(check);
        case CheckType::kLib:
            return check_lib(check);
        case CheckType::kSymbol:
            return check_symbol(check);
        case CheckType::kType:
            return check_type_check(check);
        case CheckType::kCompile:
            return check_compile(check);
        case CheckType::kDefine:
            return check_compile(check);
        case CheckType::kSizeof:
            return check_sizeof(check);
        case CheckType::kAlignof:
            return check_alignof(check);
        case CheckType::kComputeInt:
            return check_compute_int(check);
        case CheckType::kEndian:
            return check_endian(check);
        case CheckType::kDecl:
            return check_decl(check);
        case CheckType::kMember:
            return check_member(check);
        default:
            throw std::runtime_error("Unknown check type for check: " +
                                     check.define());
    }
}

CheckResult CheckRunner::check_header(const Check& check) {
    std::string header_name = check.name();
    std::string code = "#include <" + header_name + ">\n";

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

CheckResult CheckRunner::check_function(const Check& check) {
    std::string func_name = check.name();
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        code = R"(
/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.  */
#ifdef __cplusplus
extern "C"
#endif
char )" + func_name +
               R"( ();

int main(void) {
    return )" + func_name +
               R"(();
}
)";
    }

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

CheckResult CheckRunner::check_lib(const Check& check) {
    if (!check.library().has_value()) {
        throw std::runtime_error(
            "Library check missing library name for check: " + check.define());
    }

    std::string library = *check.library();
    std::string func_name = check.name();
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        // Generate default code similar to AC_CHECK_FUNC
        code = R"(
/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.  */
#ifdef __cplusplus
extern "C"
#endif
char )" + func_name +
               R"( ();

int main(void) {
    return )" + func_name +
               R"(();
}
)";
    }

    bool success = try_compile_and_link_with_lib(
        code, library, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

CheckResult CheckRunner::check_symbol(const Check& check) {
    std::string symbol_name = check.name();
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        code = R"(
int main(void) {
    #ifndef )" +
               symbol_name + R"(
    #error ")" +
               symbol_name + R"( is not defined"
    #endif
    return 0;
}
)";
    }

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

CheckResult CheckRunner::check_type_check(const Check& check) {
    std::string type_name = check.name();
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        code = R"(
int main(void) {
    if (sizeof()" +
               type_name + R"())
        return 0;
    return 1;
}
)";
    }

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

CheckResult CheckRunner::check_compile(const Check& check) {
    std::string code{};

    // Check if we have a file_path parameter
    if (check.file_path().has_value()) {
        std::string file_path = *check.file_path();
        std::ifstream file(file_path);
        if (file.is_open()) {
            std::stringstream buffer;
            buffer << file.rdbuf();
            code = buffer.str();
            file.close();
        } else {
            DebugLogger::warn("Could not open file: " + file_path);
            return CheckResult(check.define(), "0", false);
        }
    } else if (check.code().has_value()) {
        code = *check.code();
    } else {
        // Provide a default code if neither code nor file_path is provided
        code = "int main(void) { return 0; }";
    }

    bool success = try_compile(code, check.language());

    std::string value;
    if (check.define_value().has_value()) {
        value = success ? *check.define_value()
                        : (check.define_value_fail().has_value()
                               ? *check.define_value_fail()
                               : "0");
    } else {
        value = success ? "1" : "0";
    }

    return CheckResult(check.define(), value, success);
}

CheckResult CheckRunner::check_define(const Check& check) {
    std::string value =
        check.define_value().has_value() ? *check.define_value() : "1";
    return CheckResult(check.define(), value, true);
}

CheckResult CheckRunner::check_sizeof(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("sizeof check missing code for check: " +
                                 check.define());
    }

    std::string code = *check.code();

    std::optional<int> size =
        try_compile_and_run(code, check.language(), check.define());
    if (size.has_value()) {
        return CheckResult(check.define(), std::to_string(*size), true);
    } else {
        return CheckResult(check.define(), "0", false);
    }
}

CheckResult CheckRunner::check_alignof(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("alignof check missing code for check: " +
                                 check.define());
    }

    std::string code = *check.code();

    std::optional<int> alignment =
        try_compile_and_run(code, check.language(), check.define());
    if (alignment.has_value()) {
        return CheckResult(check.define(), std::to_string(*alignment), true);
    } else {
        return CheckResult(check.define(), "0", false);
    }
}

CheckResult CheckRunner::check_compute_int(const Check& check) {
    if (!check.code().has_value()) {
        DebugLogger::warn("compute_int check missing code");
        return CheckResult(check.define(), "0", false);
    }

    std::string code = *check.code();

    std::optional<int> value =
        try_compile_and_run(code, check.language(), check.define());
    if (value.has_value()) {
        return CheckResult(check.define(), std::to_string(*value), true);
    } else {
        return CheckResult(check.define(), "0", false);
    }
}

CheckResult CheckRunner::check_endian(const Check& check) {
    if (!check.code().has_value()) {
        DebugLogger::warn("endian check missing code");
        return CheckResult(check.define(), "0", false);
    }

    std::string code = *check.code();

    std::optional<int> value =
        try_compile_and_run(code, check.language(), check.define());
    if (value.has_value()) {
        return CheckResult(check.define(), std::to_string(*value), true);
    } else {
        return CheckResult(check.define(), "0", false);
    }
}

CheckResult CheckRunner::check_decl(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("decl check missing code for check: " +
                                 check.define());
    }

    std::string code = *check.code();

    bool found = try_compile(code, check.language());
    // Always mark as success=true because we always want to define the macro
    // The value (1 or 0) indicates whether the declaration was found
    return CheckResult(check.define(), found ? "1" : "0", true);
}

CheckResult CheckRunner::check_member(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("member check missing code for check: " +
                                 check.define());
    }

    std::string code = *check.code();

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

}  // namespace rules_cc_autoconf

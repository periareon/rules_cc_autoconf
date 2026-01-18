#include "autoconf/private/checker/check_runner.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <sstream>
#include <stdexcept>
#include <system_error>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/debug_logger.h"

namespace rules_cc_autoconf {

using rules_cc_autoconf::check_type_is_define;
using rules_cc_autoconf::check_type_is_subst;

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

void CheckRunner::set_required_defines(
    const std::map<std::string, std::string>& required_defines) {
    required_defines_ = required_defines;
}

std::string CheckRunner::get_defines_from_previous_checks() const {
    std::ostringstream defines;
    // Collect all successful AC_DEFINE checks (type "define") from previous
    // results and from required checks (dependencies). These need to be
    // available during compilation tests, similar to GNU Autoconf
    for (const CheckResult& result : results_) {
        // Include defines from AC_DEFINE checks (type "define")
        // Exclude subst, m4_define, and other non-compile-time defines
        if (result.is_define && result.success && !result.value.empty()) {
            defines << "#define " << result.define;
            if (result.value != "1") {
                defines << " " << result.value;
            }
            defines << "\n";
        }
    }
    // Also include defines from required checks (dependencies)
    // These are from other autoconf targets and need to be available for
    // compilation
    for (const auto& [define_name, value] : required_defines_) {
        defines << "#define " << define_name;
        if (value != "1" && !value.empty()) {
            defines << " " << value;
        }
        defines << "\n";
    }
    return defines.str();
}

std::string CheckRunner::resolve_compile_defines(const Check& check) const {
    if (!check.compile_defines().has_value()) {
        return "";
    }

    std::ostringstream defines;
    // compile_defines contains file paths to result files
    for (const std::string& result_file_path : *check.compile_defines()) {
        if (result_file_path.empty() ||
            !std::filesystem::exists(result_file_path)) {
            continue;
        }

        // Read the result file and extract successful defines
        std::ifstream results_file(result_file_path);
        if (!results_file.is_open()) {
            DebugLogger::warn("Could not open compile_defines result file: " +
                              result_file_path);
            continue;
        }

        nlohmann::json results_json{};
        try {
            results_file >> results_json;
        } catch (const std::exception& ex) {
            DebugLogger::warn("Could not parse compile_defines result file: " +
                              result_file_path + " - " + ex.what());
            results_file.close();
            continue;
        }
        results_file.close();

        // Extract all successful defines from the result file
        for (nlohmann::json::iterator it = results_json.begin();
             it != results_json.end(); ++it) {
            const std::string& define_name = it.key();
            const nlohmann::json& json_value = it.value();

            if (json_value.is_object() && json_value.contains("success") &&
                json_value["success"].is_boolean()) {
                bool success = json_value["success"].get<bool>();

                // Check is_define, define_flag, or define for backward compatibility
                bool define = true;
                if (json_value.contains("is_define") && json_value["is_define"].is_boolean()) {
                    define = json_value["is_define"].get<bool>();
                } else if (json_value.contains("define_flag") && json_value["define_flag"].is_boolean()) {
                    define = json_value["define_flag"].get<bool>();
                } else if (json_value.contains("define") && json_value["define"].is_boolean()) {
                    // Backward compatibility: check old "define" field
                    define = json_value["define"].get<bool>();
                }

                // Only include successful AC_DEFINE checks (check define flag)
                if (success && define) {
                    std::string value =
                        json_value.contains("value") &&
                                json_value["value"].is_string()
                            ? json_value["value"].get<std::string>()
                            : "";
                    if (!value.empty()) {
                        defines << "#define " << define_name << " " << value << "\n";
                    }
                }
            }
        }
    }
    return defines.str();
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
        case CheckType::kLink:
            return check_link(check);
        case CheckType::kDefine:
            return check_compile(check);
        case CheckType::kSubst:
            // Non-conditional subst - just return the value
            return check_define(check);
        case CheckType::kM4Define:
            // M4_DEFINE - compute value for requires but don't generate output
            return check_define(check);
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
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

CheckResult CheckRunner::check_link(const Check& check) {
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    // Use try_compile_and_run approach: compile to object, then link
    // We don't need to run it, just verify it links
    std::optional<int> run_result =
        try_compile_and_run(code, check.language(), check.define());
    bool success = run_result.has_value();

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
    return CheckResult(check.define(), value, true,
                       check_type_is_define(check.type()),
                       check_type_is_subst(check.type()));
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

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool found = try_compile(code, check.language(), check.define());
    // Always mark as success=true because we always want to define the macro
    // The value (1 or 0) indicates whether the declaration was found
    return CheckResult(check.define(), found ? "1" : "0", true,
                       check_type_is_define(check.type()),
                       check_type_is_subst(check.type()));
}

CheckResult CheckRunner::check_member(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("member check missing code for check: " +
                                 check.define());
    }

    std::string code = *check.code();

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool success = try_compile(code, check.language(), check.define());
    return CheckResult(check.define(), success ? "1" : "0", success);
}

}  // namespace rules_cc_autoconf

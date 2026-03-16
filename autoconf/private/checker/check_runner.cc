#include "autoconf/private/checker/check_runner.h"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <nlohmann/json.hpp>
#include <sstream>
#include <stdexcept>
#include <vector>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/checker/system_header.h"

namespace rules_cc_autoconf {

using rules_cc_autoconf::check_type_is_define;

namespace {

/**
 * @brief Get the display identifier for a check (define name if available,
 * otherwise cache variable name).
 */
std::string check_id(const Check& check) {
    return check.define().has_value() ? *check.define() : check.name();
}

}  // namespace

CheckRunner::CheckRunner(const Config& config) : config_(config) {}

void CheckRunner::set_required_defines(
    const std::map<std::string, std::string>& required_defines) {
    required_defines_ = required_defines;
}

void CheckRunner::set_dep_results(
    const std::map<std::string, CheckResult>& dep_results) {
    dep_results_ = dep_results;
}

void CheckRunner::set_source_id(const std::string& source_id,
                                const std::filesystem::path& source_dir) {
    source_id_ = source_id;
    source_dir_ = std::filesystem::path(source_dir).make_preferred();
}

std::string CheckRunner::get_defines_from_previous_checks() const {
    std::ostringstream defines;
    // Collect all successful AC_DEFINE checks (type "define") from previous
    // results and from required checks (dependencies). These need to be
    // available during compilation tests, similar to GNU Autoconf
    for (const CheckResult& result : results_) {
        // Include defines from AC_DEFINE checks (type "define")
        // Exclude subst, m4_define, and other non-compile-time defines
        if (result.is_define && result.success && result.value.has_value() &&
            !result.value->empty()) {
            std::string define_name =
                result.define.has_value() ? *result.define : result.name;
            defines << "#define " << define_name;
            if (*result.value != "1") {
                defines << " " << *result.value;
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
    // Every requested compile_define must have a paired result in dep_results_
    // (from --dep files). If missing, throw. Emit the define with the result's
    // value when we have one (including "0"); only skip when there is no value
    // to emit (empty). Ignore whether the result is a "define" type or not
    // (e.g. cache-variable-only results are fine).
    for (const std::string& define_name : *check.compile_defines()) {
        if (define_name.empty()) {
            continue;
        }

        std::map<std::string, CheckResult>::const_iterator it =
            dep_results_.find(define_name);
        if (it == dep_results_.end()) {
            throw std::runtime_error(
                "Check '" + check_id(check) + "' references compile_define '" +
                define_name +
                "' which was not found in dependent check results");
        }

        const CheckResult& result = it->second;
        if (!result.value.has_value() || result.value->empty()) {
            continue;  // No value to emit (e.g. check never ran or has no
                       // value)
        }

        std::string value = *result.value;
        defines << "#define " << define_name << " " << value << "\n";
    }
    return defines.str();
}

CheckResult CheckRunner::run_check(const Check& check) {
    DebugLogger::debug("Running check for " + check_id(check));
    switch (check.type()) {
        case CheckType::kFunction:
            return check_function(check);
        case CheckType::kLib:
            return check_lib(check);
        case CheckType::kType:
            return check_type_check(check);
        case CheckType::kCompile:
            return check_compile(check);
        case CheckType::kLink:
            return check_link(check);
        case CheckType::kDefine:
        case CheckType::kM4Variable:
            return check_define(check);
        case CheckType::kSizeof:
            return check_sizeof(check);
        case CheckType::kAlignof:
            return check_alignof(check);
        case CheckType::kComputeInt:
            return check_compute_int(check);
        case CheckType::kDecl:
            return check_decl(check);
        case CheckType::kMember:
            return check_member(check);
        case CheckType::kGlNextHeader:
            return check_gl_next_header(check);
        default:
            throw std::runtime_error("Unknown check type for check: " +
                                     check_id(check));
    }
}

CheckResult CheckRunner::check_function(const Check& check) {
    // For AC_CHECK_FUNC, check.name() is the cache variable (e.g.,
    // "ac_cv_func_malloc") We need to extract the actual function name from it
    std::string cache_var = check.name();
    std::string func_name;

    // Extract function name from cache variable name
    // Pattern: "ac_cv_func_<function>" -> "<function>"
    if (cache_var.rfind("ac_cv_func_", 0) == 0) {
        func_name = cache_var.substr(11);  // Skip "ac_cv_func_"
    } else {
        // Fallback: assume name is the function name (for custom cache variable
        // names)
        func_name = cache_var;
    }

    std::string code{};
    if (!check.code().has_value()) {
        throw std::runtime_error("Function check missing code: " +
                                 check_id(check));
    }
    code = *check.code();

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    // AC_CHECK_FUNC should use linking (not just compilation) to match GNU
    // Autoconf behavior This ensures functions that exist but aren't declared
    // in headers are detected
    bool success = try_compile_and_link(code, check.language());
    return CheckResult(check.name(), success ? "1" : "0", success,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst());
}

CheckResult CheckRunner::check_lib(const Check& check) {
    if (!check.library().has_value()) {
        throw std::runtime_error(
            "Library check missing library name for check: " + check_id(check));
    }

    std::string library = *check.library();
    std::string func_name = check.name();
    std::string code{};

    if (!check.code().has_value()) {
        throw std::runtime_error("Library check missing code: " +
                                 check_id(check));
    }
    code = *check.code();

    bool success =
        try_compile_and_link_with_lib(code, library, check.language());
    return CheckResult(check.name(), success ? "1" : "0", success,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst());
}

CheckResult CheckRunner::check_type_check(const Check& check) {
    std::string type_name = check.name();
    std::string code{};

    if (!check.code().has_value()) {
        throw std::runtime_error("Type check missing code: " + check_id(check));
    }
    code = *check.code();

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool success = try_compile(code, check.language());
    return CheckResult(check.name(), success ? "1" : "0", success,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst());
}

CheckResult CheckRunner::check_compile(const Check& check) {
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        code = "int main(void) { return 0; }";
    }

    // Resolve compile_defines and prepend to code (same as config rendering:
    // only successful defines are added; failed deps are skipped)
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool success = try_compile(code, check.language());

    std::optional<std::string> value;
    bool should_output = true;
    if (check.define_value().has_value()) {
        value = success ? *check.define_value()
                        : (check.define_value_fail().has_value()
                               ? *check.define_value_fail()
                               : std::string("0"));
    } else {
        // define_value is not set
        if (success) {
            // Check succeeded - if define_value_fail is set, it means we only
            // want to define on failure So don't output when success is true
            if (check.define_value_fail().has_value()) {
                should_output = false;
                value = std::nullopt;
            } else {
                // Neither define_value nor define_value_fail is set - use
                // default "1"
                value = std::string("1");
            }
        } else {
            // Check failed - use define_value_fail if set, otherwise "0"
            value = check.define_value_fail().has_value()
                        ? std::optional<std::string>(*check.define_value_fail())
                        : std::optional<std::string>("0");
        }
    }

    return CheckResult(check.name(), value, should_output ? success : false,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst());
}

CheckResult CheckRunner::check_link(const Check& check) {
    std::string code{};

    if (check.code().has_value()) {
        code = *check.code();
    } else {
        code = "int main(void) { return 0; }";
    }

    // Resolve compile_defines and prepend to code (same as config rendering:
    // only successful defines are added; failed deps are skipped)
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    // Compile and link without running - we only need to verify linking
    // succeeds
    bool success = try_compile_and_link(code, check.language());

    std::string value;
    if (check.define_value().has_value()) {
        value = success ? *check.define_value()
                        : (check.define_value_fail().has_value()
                               ? *check.define_value_fail()
                               : "0");
    } else {
        value = success ? "1" : "0";
    }

    return CheckResult(
        check.name(), value, success, check_type_is_define(check.type()),
        check.subst().has_value(), check.type(), check.define(), check.subst());
}

CheckResult CheckRunner::check_define(const Check& check) {
    std::string value;
    if (check.define_value().has_value()) {
        value = *check.define_value();
    } else {
        // If define_value is not set (None/null), use empty string to render as
        // /**/ If define_value is not provided at all (defaults to 1), this
        // won't be reached because macros.bzl will set it to 1 by default
        value = "";
    }
    return CheckResult(check.name(), value, true,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst(), check.unquote());
}

CheckResult CheckRunner::check_sizeof(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("sizeof check missing code for check: " +
                                 check_id(check));
    }

    std::string code_template = *check.code();

    // Resolve compile_defines and prepend to code template
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code_template = defines_code + code_template;
    }

    // Use static_assert to find the sizeof value at compile time
    std::optional<int> size = find_compile_time_value_with_static_assert(
        code_template, check.language());

    if (size.has_value()) {
        return CheckResult(check.name(), std::to_string(*size), true,
                           check_type_is_define(check.type()),
                           check.subst().has_value(), check.type(),
                           check.define(), check.subst());
    } else {
        return CheckResult(check.name(), "0", false,
                           check_type_is_define(check.type()),
                           check.subst().has_value(), check.type(),
                           check.define(), check.subst());
    }
}

CheckResult CheckRunner::check_alignof(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("alignof check missing code for check: " +
                                 check_id(check));
    }

    std::string code_template = *check.code();

    // Resolve compile_defines and prepend to code template
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code_template = defines_code + code_template;
    }

    // Use static_assert to find the alignment value at compile time
    std::optional<int> alignment = find_compile_time_value_with_static_assert(
        code_template, check.language());

    if (alignment.has_value()) {
        return CheckResult(check.name(), std::to_string(*alignment), true,
                           check_type_is_define(check.type()),
                           check.subst().has_value(), check.type(),
                           check.define(), check.subst());
    } else {
        return CheckResult(check.name(), "0", false,
                           check_type_is_define(check.type()),
                           check.subst().has_value(), check.type(),
                           check.define(), check.subst());
    }
}

CheckResult CheckRunner::check_compute_int(const Check& check) {
    std::string id = check_id(check);
    if (!check.code().has_value()) {
        DebugLogger::warn("compute_int check missing code");
        return CheckResult(id, "0", false, check_type_is_define(check.type()),
                           check.subst().has_value(), check.type());
    }

    std::optional<int> value =
        find_compile_time_int_bisect(*check.code(), check.language());

    if (value.has_value()) {
        return CheckResult(id, std::to_string(*value), true,
                           check_type_is_define(check.type()),
                           check.subst().has_value(), check.type());
    }
    return CheckResult(id, "0", false, check_type_is_define(check.type()),
                       check.subst().has_value(), check.type());
}

CheckResult CheckRunner::check_decl(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("decl check missing code for check: " +
                                 check_id(check));
    }

    std::string code = *check.code();

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool found = try_compile(code, check.language());

    std::optional<std::string> value;
    if (check.define_value().has_value()) {
        if (found) {
            value = *check.define_value();
        } else {
            if (check.define_value_fail().has_value()) {
                value = *check.define_value_fail();
            } else {
                value = std::nullopt;
            }
        }
    } else {
        if (found) {
            value = std::string("1");
        } else {
            value = std::nullopt;
        }
    }

    // Set success based on whether the declaration was found
    // This allows requires conditions to correctly evaluate based on
    // declaration presence When found=false, success=false, so requires will
    // fail and dependent checks are skipped However, the resolver will still
    // create the define if define_value_fail was set (value.has_value())
    return CheckResult(
        check.name(), value, found, check_type_is_define(check.type()),
        check.subst().has_value(), check.type(), check.define(), check.subst());
}

CheckResult CheckRunner::check_member(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error("member check missing code for check: " +
                                 check_id(check));
    }

    std::string code = *check.code();

    // Resolve compile_defines and prepend to code
    std::string defines_code = resolve_compile_defines(check);
    if (!defines_code.empty()) {
        code = defines_code + code;
    }

    bool success = try_compile(code, check.language());
    return CheckResult(check.name(), success ? "1" : "0", success,
                       check_type_is_define(check.type()),
                       check.subst().has_value(), check.type(), check.define(),
                       check.subst());
}

CheckResult CheckRunner::check_gl_next_header(const Check& check) {
    if (!check.code().has_value()) {
        throw std::runtime_error(
            "GL_NEXT_HEADER check missing 'code' (header name) for check: " +
            check_id(check));
    }

    std::string header = *check.code();
    DebugLogger::debug("GL_NEXT_HEADER: resolving " + header);

    // Look up INCLUDE_NEXT from dependency results to determine strategy
    auto it = dep_results_.find("INCLUDE_NEXT");
    bool have_include_next = it != dep_results_.end() &&
                             it->second.value.has_value() &&
                             !it->second.value->empty();

    if (have_include_next) {
        // GCC/Clang: #include_next is supported, use angle-bracket include
        std::string value = "<" + header + ">";
        DebugLogger::debug("GL_NEXT_HEADER: include_next supported, value=" +
                           value);
        return CheckResult(check.name(), value, true, false, true, check.type(),
                           check.define(), check.subst());
    }

    // #include_next not supported -- inline the system header content
    std::string compiler =
        (check.language() == "cpp" || check.language() == "c++")
            ? config_.cpp_compiler
            : config_.c_compiler;
    std::vector<std::string> flags =
        (check.language() == "cpp" || check.language() == "c++")
            ? config_.cpp_flags
            : config_.c_flags;

    auto sys_path =
        find_system_header_path(compiler, flags, config_.compiler_type, header,
                                source_id_, source_dir_);

    bool msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    if (!sys_path.has_value()) {
        // MSVC: return empty so the template's `# @INCLUDE_NEXT@ @NEXT_*@`
        // becomes `# ` (a valid null directive).  Returning `<header>` would
        // produce `# <header>` which MSVC rejects as an invalid preprocessor
        // directive.  Headers absent from the platform are guarded by `#if 0`
        // in gnulib templates.
        std::string value = msvc ? "" : "<" + header + ">";
        DebugLogger::debug("GL_NEXT_HEADER: system header not found for " +
                           header + ", returning " +
                           (value.empty() ? "empty" : value));
        return CheckResult(check.name(), value, true, false, true, check.type(),
                           check.define(), check.subst());
    }

    auto content = read_file_content(*sys_path);
    if (!content.has_value()) {
        DebugLogger::warn("GL_NEXT_HEADER: could not read system header at " +
                          sys_path->string());
        std::string value = msvc ? "" : "<" + header + ">";
        return CheckResult(check.name(), value, true, false, true, check.type(),
                           check.define(), check.subst());
    }

    // Prepend a newline so the template's `# @INCLUDE_NEXT@ @NEXT_*@` becomes
    // `# ` (null directive) followed by the inlined content on the next line
    std::string value = "\n" + *content;
    DebugLogger::debug("GL_NEXT_HEADER: inlined " +
                       std::to_string(content->size()) + " bytes from " +
                       sys_path->string());
    return CheckResult(check.name(), value, true, false, true, check.type(),
                       check.define(), check.subst());
}

std::optional<int> CheckRunner::find_compile_time_value_with_static_assert(
    const std::string& base_code_template, const std::string& language) {
    std::vector<int> values_to_try = {1,  2,   4,   8,   16,  32,
                                      64, 128, 256, 512, 1024};

    for (int value : values_to_try) {
        std::string code = base_code_template;
        std::string value_str = std::to_string(value);
        size_t pos = 0;
        while ((pos = code.find("{value}", pos)) != std::string::npos) {
            code.replace(pos, 7, value_str);
            pos += value_str.length();
        }

        if (try_compile(code, language)) {
            return value;
        }
    }

    return std::nullopt;
}

static std::string gen_less_compare(const std::string& base_code_template,
                                    const std::string& lhs,
                                    const std::string& rhs) {
    std::string code = base_code_template;
    bool found_lhs = false;
    for (size_t pos = code.find("{lhs}"); pos != std::string::npos;
         pos = code.find("{lhs}", pos)) {
        code.replace(pos, 5, lhs);
        pos += lhs.length();
        found_lhs = true;
    }
    if (!found_lhs) {
        throw std::runtime_error(
            "Code template must contain '{lhs}' placeholder for static_assert "
            "checks");
    }
    bool found_rhs = false;
    for (size_t pos = code.find("{rhs}"); pos != std::string::npos;
         pos = code.find("{rhs}", pos)) {
        code.replace(pos, 5, rhs);
        pos += rhs.length();
        found_rhs = true;
    }
    if (!found_rhs) {
        throw std::runtime_error(
            "Code template must contain '{rhs}' placeholder for static_assert "
            "checks");
    }
    return code;
}

static std::pair<std::string, std::string> split_code_expr(
    const std::string& base_code_template) {
    const size_t begin = base_code_template.find('{');
    const char* const error =
        "Code template must contains '{$EXPR}' placeholder for expr value "
        "evaluation";
    if (begin == std::string::npos) {
        throw std::runtime_error(error);
    }

    const size_t end = base_code_template.find('}', begin + 1);
    if (end == std::string::npos) {
        throw std::runtime_error(error);
    }

    const std::string expr =
        base_code_template.substr(begin + 1, end - begin - 1);

    if (expr.empty()) {
        throw std::runtime_error(error);
    }

    std::string code = base_code_template;
    code.replace(begin, end - begin + 1, "");
    return {code, expr};
}

std::optional<int> CheckRunner::find_compile_time_int_bisect(
    const std::string& base_code_template, const std::string& language,
    const int search_begin, const int search_end) {
    const std::pair<std::string, std::string> code_expr =
        split_code_expr(base_code_template);
    // int type for target might not be same as host
    // let's assume the value we detect (usually pre-defined constant value for
    // syscall) live in sensible range
    //

    if (try_compile(gen_less_compare(code_expr.first, code_expr.second,
                                     std::to_string(search_begin)),
                    language) ||
        try_compile(
            gen_less_compare(code_expr.first, std::to_string(search_end),
                             code_expr.second),
            language)) {
        // value out of host int range, give up
        throw std::runtime_error(
            "Unable to determine compile-time value for constant '" +
            code_expr.second + "' because it is outside the search range " +
            std::to_string(search_begin) + " ~ " + std::to_string(search_end));
    }
    // both compile false, might also indicate no such constant exist
    if (!try_compile(
            gen_less_compare(code_expr.first, std::to_string(search_begin),
                             code_expr.second),
            language) &&
        !try_compile(gen_less_compare(code_expr.first, code_expr.second,
                                      std::to_string(search_end)),
                     language)) {
        // at least search_begin < constant or constant < search_end should
        // compile if none compile, means expr can't evaluate at compile time
        throw std::runtime_error(
            "'" + code_expr.second +
            "' can't be evaluated (non-exist constant, invalid expression, or "
            "can't evaluate at compile time)");
    }

    int l = search_begin;
    int r = search_end;

    // begin <= current value <= end
    while (l < r) {
        // search_end will decrease by middle - 1
        // search_begin will increase with middle
        // when search_begin + 1 = search_end, we choose middle = search_end
        // so range will always shrink
        // use delta/2 + begin to avoid int sum overflow
        int middle = l + (r - l + 1) / 2;

        // we use current_value < middle to detect range

        std::string code = gen_less_compare(code_expr.first, code_expr.second,
                                            std::to_string(middle));

        if (try_compile(code, language)) {
            // value < middle
            r = middle - 1;
        } else {
            // middle <= value
            l = middle;
        }
    }

    assert(l == r);
    return l;
}

}  // namespace rules_cc_autoconf

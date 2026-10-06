#include "autoconf/private/checker/int_bisect.h"

#include <cassert>
#include <stdexcept>
#include <string>

namespace rules_cc_autoconf {

std::string gen_less_compare(const std::string& code_template,
                             const std::string& lhs, const std::string& rhs) {
    std::string code = code_template;
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

std::pair<std::string, std::string> split_code_expr(
    const std::string& base_code_template) {
    const size_t begin = base_code_template.find("{$");
    const char* const error =
        "Code template must contain '{$EXPR}' placeholder for expr value "
        "evaluation";
    if (begin == std::string::npos) {
        throw std::runtime_error(error);
    }

    // Skip past the `{$` prefix; the expression starts after it.
    const size_t expr_start = begin + 2;
    const size_t end = base_code_template.find('}', expr_start);
    if (end == std::string::npos) {
        throw std::runtime_error(error);
    }

    const std::string expr =
        base_code_template.substr(expr_start, end - expr_start);

    if (expr.empty()) {
        throw std::runtime_error(error);
    }

    std::string code = base_code_template;
    code.replace(begin, end - begin + 1, "");
    return {code, expr};
}

int bisect_compile_time_int(const std::string& base_code_template,
                            const int search_begin, const int search_end,
                            const CompileProbe& compiles) {
    const std::pair<std::string, std::string> code_expr =
        split_code_expr(base_code_template);
    const std::string& code = code_expr.first;
    const std::string& expr = code_expr.second;
    // int type for target might not be same as host
    // let's assume the value we detect (usually pre-defined constant value for
    // syscall) live in sensible range

    if (compiles(gen_less_compare(code, expr, std::to_string(search_begin))) ||
        compiles(gen_less_compare(code, std::to_string(search_end), expr))) {
        // value out of host int range, give up
        throw std::runtime_error(
            "Unable to determine compile-time value for constant '" + expr +
            "' because it is outside the search range " +
            std::to_string(search_begin) + " ~ " + std::to_string(search_end));
    }
    // both compile false, might also indicate no such constant exist
    if (!compiles(gen_less_compare(code, std::to_string(search_begin), expr)) &&
        !compiles(gen_less_compare(code, expr, std::to_string(search_end)))) {
        // at least search_begin < constant or constant < search_end should
        // compile if none compile, means expr can't evaluate at compile time
        throw std::runtime_error(
            "'" + expr +
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
        if (compiles(gen_less_compare(code, expr, std::to_string(middle)))) {
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

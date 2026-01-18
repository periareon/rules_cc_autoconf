#pragma once

#include <string>

namespace rules_cc_autoconf {

/**
 * @brief Result of a configuration check.
 *
 * Contains the preprocessor define name, its value, and whether the check
 * succeeded.
 */
struct CheckResult {
    /** Preprocessor define name (e.g., "HAVE_STDIO_H") */
    std::string define{};

    /** Define value (e.g., "1" or "0") */
    std::string value{};

    /** Whether the check succeeded */
    bool success = false;

    /** Whether this is a define (true for all CheckTypes except kM4Define and kSubst) */
    bool is_define = false;

    /** Whether this is a subst (true only for kSubst) */
    bool is_subst = false;

    /**
     * @brief Construct a CheckResult.
     * @param define The preprocessor define name.
     * @param value The define value.
     * @param success Whether the check succeeded.
     * @param is_define Whether this is a define (default: true).
     * @param is_subst Whether this is a subst (default: false).
     */
    CheckResult(const std::string& define, const std::string& value,
                bool success, bool is_define = true, bool is_subst = false)
        : define(define), value(value), success(success), is_define(is_define), is_subst(is_subst) {}
};

}  // namespace rules_cc_autoconf

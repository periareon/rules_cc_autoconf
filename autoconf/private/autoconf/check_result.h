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

    /**
     * @brief Construct a CheckResult.
     * @param define The preprocessor define name.
     * @param value The define value.
     * @param success Whether the check succeeded.
     */
    CheckResult(const std::string& define, const std::string& value,
                bool success)
        : define(define), value(value), success(success) {}
};

}  // namespace rules_cc_autoconf

#pragma once

#include <string>
#include <optional>

#include "autoconf/private/checker/check.h"

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

    /** Whether this is a define (true for all CheckTypes except kM4Variable) */
    bool is_define = false;

    /** Whether this is a subst (true when subst field is set on the check) */
    bool is_subst = false;

    /** The type of check that produced this result */
    CheckType type = CheckType::kDefine;

    /**
     * @brief Construct a CheckResult.
     * @param define The preprocessor define name.
     * @param value The define value.
     * @param success Whether the check succeeded.
     * @param is_define Whether this is a define (default: true).
     * @param is_subst Whether this is a subst (default: false).
     * @param type The type of check that produced this result (default: kDefine).
     */
    CheckResult(const std::string& define, const std::string& value,
                bool success, bool is_define = true, bool is_subst = false,
                CheckType type = CheckType::kDefine)
        : define(define), value(value), success(success), is_define(is_define), is_subst(is_subst),
          type(type) {}


    /**
     * @brief Parse a CheckResult from JSON.
     * @param define_name The define name (typically the JSON key).
     * @param json_value The JSON value object containing success, value, is_define, is_subst.
     * @return CheckResult object, or std::nullopt if parsing failed.
     */
    static std::optional<CheckResult> from_json(const std::string& define_name, const void* json_value);
};

}  // namespace rules_cc_autoconf

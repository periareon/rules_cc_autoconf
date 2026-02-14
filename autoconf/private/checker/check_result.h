#pragma once

#include <optional>
#include <string>

#include "autoconf/private/checker/check.h"

namespace rules_cc_autoconf {

/**
 * @brief Result of a configuration check.
 *
 * Contains the cache variable name, its value, and whether the check
 * succeeded.
 */
struct CheckResult {
    /** Cache variable name (e.g., "ac_cv_func_printf") */
    std::string name{};

    /** Optional define name from the check (e.g., "HAVE_PRINTF") */
    std::optional<std::string> define{};

    /** Optional subst name from the check (e.g., "HAVE_PRINTF") */
    std::optional<std::string> subst{};

    /**
     * Define value (e.g., "1" or "0").
     * nullopt means no value was provided (renders as /​* #undef *​/).
     * Empty string means a value was explicitly set to empty (renders as
     * #define VAR /​**​/).
     */
    std::optional<std::string> value{};

    /** Whether the check succeeded */
    bool success = false;

    /** Whether this is a define (true for all CheckTypes except kM4Variable) */
    bool is_define = false;

    /** Whether this is a subst (true when subst field is set on the check) */
    bool is_subst = false;

    /** The type of check that produced this result */
    CheckType type = CheckType::kDefine;

    /** Whether this is an unquoted define (AC_DEFINE_UNQUOTED) */
    bool unquote = false;

    /**
     * @brief Construct a CheckResult.
     * @param name The cache variable name (e.g., "ac_cv_func_printf").
     * @param value The define value. nullopt = not provided, "" = explicitly
     * empty.
     * @param success Whether the check succeeded.
     * @param is_define Whether this is a define (default: true).
     * @param is_subst Whether this is a subst (default: false).
     * @param type The type of check that produced this result (default:
     * kDefine).
     * @param define Optional define name from the check.
     * @param subst Optional subst name from the check.
     * @param unquote Whether this is AC_DEFINE_UNQUOTED (default: false).
     */
    CheckResult(const std::string& name,
                const std::optional<std::string>& value, bool success,
                bool is_define = true, bool is_subst = false,
                CheckType type = CheckType::kDefine,
                std::optional<std::string> define = std::nullopt,
                std::optional<std::string> subst = std::nullopt,
                bool unquote = false)
        : name(name),
          define(define),
          subst(subst),
          value(value),
          success(success),
          is_define(is_define),
          is_subst(is_subst),
          type(type),
          unquote(unquote) {}

    /**
     * @brief Parse a CheckResult from JSON.
     * @param name The cache variable name (typically the JSON key).
     * @param json_value The JSON value object containing success, value,
     * is_define, is_subst, define, subst.
     * @return CheckResult object, or std::nullopt if parsing failed.
     */
    static std::optional<CheckResult> from_json(const std::string& name,
                                                const void* json_value);
};

}  // namespace rules_cc_autoconf

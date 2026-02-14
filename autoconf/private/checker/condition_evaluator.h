#pragma once

#include <map>
#include <stdexcept>
#include <string>

#include "autoconf/private/checker/check_result.h"

namespace rules_cc_autoconf {

/**
 * @brief Evaluates condition expressions for conditional checks.
 *
 * Parses condition strings like "FOO", "!FOO", "FOO==1", "FOO!=0" and evaluates
 * them against a map of check results.
 *
 * Negation prefix (!): "!FOO" is true when FOO has success=false or a falsy
 * value (0, empty); false when FOO has a truthy value (1, "yes", etc.).
 *
 * Compares JSON-encoded values for value-based comparisons (==, !=).
 */
class ConditionEvaluator {
   public:
    /**
     * @brief Construct a condition evaluator from a condition expression.
     * @param condition_expr Condition expression (e.g., "FOO", "!FOO",
     * "FOO==1", "FOO!=0")
     */
    explicit ConditionEvaluator(const std::string& condition_expr);

    /**
     * @brief Compute whether the condition is true given a map of dependent
     * check results.
     * @param dep_results Map of results from dependent checks.
     * @return true if condition is satisfied, false otherwise.
     * @throws std::runtime_error if the condition's define is not found in the
     * map.
     */
    bool compute(const std::map<std::string, CheckResult>& dep_results) const;

    /**
     * @brief Get the define name this condition references.
     * @return The define name.
     */
    const std::string& define_name() const { return define_name_; }

    /**
     * @brief Check if this condition has a value comparison.
     * @return true if condition uses == or !=, false if it's just existence
     * check.
     */
    bool has_value_compare() const { return has_value_compare_; }

    /**
     * @brief Check if this condition uses != for value comparison.
     * @return true if condition uses !=, false otherwise.
     */
    bool is_negated() const { return value_negated_; }

    /**
     * @brief Check if this condition has a leading ! prefix (!FOO).
     * @return true if condition was "!FOO" (result is negated), false
     * otherwise.
     */
    bool has_condition_negation_prefix() const { return condition_negated_; }

    /**
     * @brief Get the comparison value (for == or != conditions).
     * @return The value to compare against, or empty string if no value
     * comparison.
     */
    const std::string& comparison_value() const { return cond_value_; }

    /**
     * @brief Check if an expression has a negation prefix (!FOO).
     * @param expr The expression to check.
     * @return true if expression starts with '!' (e.g., "!FOO"), false
     * otherwise.
     */
    static bool has_negation_prefix(const std::string& expr);

    /**
     * @brief Strip negation prefix from an expression.
     * @param expr The expression (e.g., "!FOO").
     * @return The expression without the negation prefix (e.g., "FOO").
     */
    static std::string strip_negation_prefix(const std::string& expr);

    /**
     * @brief Find the condition's result in a map of check results.
     * @param results Map of define names to their check results.
     * @return Pointer to the CheckResult for the condition's define.
     * @throws std::runtime_error if the condition's define is not found in
     * results.
     */
    const CheckResult* find_condition_result(
        const std::map<std::string, CheckResult>& results) const;

    /**
     * @brief Evaluate the condition against a check result.
     * @param result The check result to evaluate against.
     * @return true if condition is satisfied, false otherwise.
     */
    bool evaluate(const CheckResult* result) const;

   private:
    std::string define_name_{};
    std::string cond_value_{};
    bool has_value_compare_ = false;
    bool value_negated_ = false;
    /** true when condition had leading ! (e.g. "!FOO") - final result is
     * negated */
    bool condition_negated_ = false;
};

}  // namespace rules_cc_autoconf

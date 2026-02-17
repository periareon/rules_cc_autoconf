#include "autoconf/private/checker/condition_evaluator.h"

#include "autoconf/private/checker/debug_logger.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

ConditionEvaluator::ConditionEvaluator(const std::string& condition_expr)
    : define_name_(),
      cond_value_(),
      has_value_compare_(false),
      value_negated_(false),
      condition_negated_(false) {
    // Strip leading ! prefix (e.g. "!FOO" -> "FOO"); result will be negated in
    // compute()
    std::string cond_expr = condition_expr;
    if (has_negation_prefix(cond_expr)) {
        cond_expr = strip_negation_prefix(cond_expr);
        condition_negated_ = true;
    }

    // Parse condition - may be "DEFINE_NAME", "DEFINE_NAME==value",
    // or "DEFINE_NAME!=value"
    define_name_ = cond_expr;

    // Check for != operator first (e.g., "FOO!=1")
    size_t neq_pos = cond_expr.find("!=");
    if (neq_pos != std::string::npos) {
        cond_value_ = cond_expr.substr(neq_pos + 2);
        define_name_ = cond_expr.substr(0, neq_pos);
        has_value_compare_ = true;
        value_negated_ = true;
    } else {
        // Check for == operator (e.g., "FOO==1")
        // Note: "FOO=1" (single =) is also supported and treated identically to
        // "FOO==1"
        size_t eq2_pos = cond_expr.find("==");
        if (eq2_pos != std::string::npos) {
            cond_value_ = cond_expr.substr(eq2_pos + 2);
            define_name_ = cond_expr.substr(0, eq2_pos);
            has_value_compare_ = true;
        } else {
            // Check for = operator (e.g., "FOO=1")
            // This is treated the same as "FOO==1" - both perform equality
            // comparison
            size_t eq_pos = cond_expr.find('=');
            if (eq_pos != std::string::npos) {
                cond_value_ = cond_expr.substr(eq_pos + 1);
                define_name_ = cond_expr.substr(0, eq_pos);
                has_value_compare_ = true;
            }
        }
    }

    if (DebugLogger::is_verbose_debug_enabled()) {
        DebugLogger::debug(
            "ConditionEvaluator parsed: define_name='" + define_name_ +
            "', condition_negated=" +
            std::string(condition_negated_ ? "true" : "false") +
            ", has_value_compare=" +
            std::string(has_value_compare_ ? "true" : "false") +
            ", value_negated=" +
            std::string(value_negated_ ? "true" : "false") +
            (has_value_compare_ ? ", cond_value='" + cond_value_ + "'" : ""));
    }
}

const CheckResult* ConditionEvaluator::find_condition_result(
    const std::map<std::string, CheckResult>& results) const {
    // First try to find by the define name directly (in case map is keyed by
    // define name)
    auto it = results.find(define_name_);
    if (it != results.end()) {
        return &it->second;
    }

    // If not found, search by iterating through results to find a match by
    // define name The results map is keyed by cache variable names, but we need
    // to find by define name
    for (const auto& [key, result] : results) {
        // Check if this result's define name matches
        if (result.define.has_value() && *result.define == define_name_) {
            return &result;
        }
        // Check if this result's subst name matches
        if (result.subst.has_value() && *result.subst == define_name_) {
            return &result;
        }
        // Also check if the cache variable name matches (for backward
        // compatibility)
        if (result.name == define_name_) {
            return &result;
        }
    }

    std::string error =
        "Condition references '" + define_name_ +
        "' which was not found in check results. Available options are: ";
    for (const auto& [key, _] : results) {
        error += " `" + key + "`";
    }

    throw std::runtime_error(error);
}

bool ConditionEvaluator::compute(
    const std::map<std::string, CheckResult>& dep_results) const {
    // Log the condition being checked
    if (DebugLogger::is_debug_enabled()) {
        std::string condition_str = condition_negated_ ? "!" : "";
        condition_str += define_name_;
        if (has_value_compare_) {
            std::string operator_str = value_negated_ ? "!=" : "==";
            condition_str += operator_str + cond_value_;
        }
        DebugLogger::debug("Checking condition: " + condition_str);
    }

    // Look up the condition's result from dependent checks
    const CheckResult* cond_result_ptr = find_condition_result(dep_results);

    // Evaluate condition; negate result when condition had leading ! (!FOO)
    bool result = evaluate(cond_result_ptr);
    return condition_negated_ ? !result : result;
}

bool ConditionEvaluator::evaluate(const CheckResult* result) const {
    if (result == nullptr) {
        return false;
    }

    // Evaluate condition
    bool cond_true = false;
    if (has_value_compare_) {
        // Value comparison: compare JSON-encoded values
        // Values in CheckResult are stored as JSON-encoded strings (from
        // json.dump()) The condition value (cond_value_) is a plain string that
        // needs to be JSON-encoded for comparison. To compare with a string
        // literal, use "FOO==\"1\"" (with escaped quotes)
        nlohmann::json cond_value_json;
        nlohmann::json actual_value_json;
        std::string actual_value_str = result->value.value_or("");
        try {
            // Parse the condition value as JSON (e.g., "0" -> 0, "\"1\"" ->
            // "1")
            cond_value_json = nlohmann::json::parse(cond_value_);
            // Parse the actual value as JSON (e.g., "1" -> 1, "\"1\"" -> "1")
            actual_value_json = nlohmann::json::parse(actual_value_str);
        } catch (const nlohmann::json::parse_error&) {
            // If parsing fails, treat as plain strings and JSON-encode them
            cond_value_json = cond_value_;
            actual_value_json = actual_value_str;
        }

        // Compare JSON-encoded representations
        // Both values are parsed as JSON, so we compare their JSON-encoded
        // string representations This ensures consistent comparison regardless
        // of whether the original value was stored as a number, string, or
        // other JSON type
        std::string cond_value_encoded = cond_value_json.dump();
        std::string actual_value_encoded = actual_value_json.dump();
        bool value_matches = (actual_value_encoded == cond_value_encoded);
        if (value_negated_) {
            cond_true = !value_matches;
        } else {
            cond_true = value_matches;
        }

        if (DebugLogger::is_debug_enabled()) {
            std::string final_result = cond_true ? "true" : "false";
            DebugLogger::debug("Condition comparison: (" +
                               actual_value_encoded + ")" + define_name_ +
                               (value_negated_ ? "!=" : "==") +
                               cond_value_encoded + " === " + final_result);
        }
    } else {
        // Simple condition: check if check succeeded with a
        // truthy value (non-empty, non-zero)
        cond_true = result->success && result->value.has_value() &&
                    !result->value->empty() && *result->value != "0";
    }

    return cond_true;
}

bool ConditionEvaluator::has_negation_prefix(const std::string& expr) {
    return !expr.empty() && expr[0] == '!';
}

std::string ConditionEvaluator::strip_negation_prefix(const std::string& expr) {
    if (has_negation_prefix(expr)) {
        return expr.substr(1);
    }
    return expr;
}

}  // namespace rules_cc_autoconf

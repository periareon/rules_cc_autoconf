#include "autoconf/private/checker/condition_evaluator.h"

#include "autoconf/private/checker/debug_logger.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

const char* compare_op_str(CompareOp op) {
    switch (op) {
        case CompareOp::kNone:
            return "";
        case CompareOp::kEqual:
            return "==";
        case CompareOp::kNotEqual:
            return "!=";
        case CompareOp::kLessThan:
            return "<";
        case CompareOp::kGreaterThan:
            return ">";
        case CompareOp::kLessEqual:
            return "<=";
        case CompareOp::kGreaterEqual:
            return ">=";
    }
    return "";
}

namespace {

struct OperatorMatch {
    const char* token;
    size_t token_len;
    CompareOp op;
};

// Order matters: multi-char operators before their single-char prefixes.
constexpr OperatorMatch kOperators[] = {
    {"<=", 2, CompareOp::kLessEqual}, {">=", 2, CompareOp::kGreaterEqual},
    {"!=", 2, CompareOp::kNotEqual},  {"==", 2, CompareOp::kEqual},
    {"<", 1, CompareOp::kLessThan},   {">", 1, CompareOp::kGreaterThan},
    {"=", 1, CompareOp::kEqual},
};

}  // namespace

ConditionEvaluator::ConditionEvaluator(const std::string& condition_expr)
    : define_name_(),
      cond_value_(),
      compare_op_(CompareOp::kNone),
      condition_negated_(false) {
    std::string cond_expr = condition_expr;
    if (has_negation_prefix(cond_expr)) {
        cond_expr = strip_negation_prefix(cond_expr);
        condition_negated_ = true;
    }

    define_name_ = cond_expr;

    for (const auto& entry : kOperators) {
        size_t pos = cond_expr.find(entry.token);
        if (pos != std::string::npos) {
            define_name_ = cond_expr.substr(0, pos);
            cond_value_ = cond_expr.substr(pos + entry.token_len);
            compare_op_ = entry.op;
            break;
        }
    }

    if (DebugLogger::is_verbose_debug_enabled()) {
        DebugLogger::debug(
            "ConditionEvaluator parsed: define_name='" + define_name_ +
            "', condition_negated=" +
            std::string(condition_negated_ ? "true" : "false") +
            ", compare_op=" + compare_op_str(compare_op_) +
            (has_value_compare() ? ", cond_value='" + cond_value_ + "'" : ""));
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
    if (DebugLogger::is_debug_enabled()) {
        std::string condition_str = condition_negated_ ? "!" : "";
        condition_str += define_name_;
        if (has_value_compare()) {
            condition_str += compare_op_str(compare_op_) + cond_value_;
        }
        DebugLogger::debug("Checking condition: " + condition_str);
    }

    const CheckResult* cond_result_ptr = find_condition_result(dep_results);

    bool result = evaluate(cond_result_ptr);
    return condition_negated_ ? !result : result;
}

bool ConditionEvaluator::evaluate(const CheckResult* result) const {
    if (result == nullptr) {
        return false;
    }

    if (!has_value_compare()) {
        return result->success && result->value.has_value() &&
               !result->value->empty() && *result->value != "0";
    }

    std::string actual_value_str = result->value.value_or("");

    // Relational operators use integer comparison.
    if (compare_op_ == CompareOp::kLessThan ||
        compare_op_ == CompareOp::kGreaterThan ||
        compare_op_ == CompareOp::kLessEqual ||
        compare_op_ == CompareOp::kGreaterEqual) {
        int actual_int = 0;
        int cond_int = 0;
        try {
            actual_int = std::stoi(actual_value_str);
            cond_int = std::stoi(cond_value_);
        } catch (const std::exception& e) {
            throw std::runtime_error("Relational condition '" + define_name_ +
                                     compare_op_str(compare_op_) + cond_value_ +
                                     "' requires integer values, got actual='" +
                                     actual_value_str + "', expected='" +
                                     cond_value_ + "': " + e.what());
        }

        bool cond_true = false;
        switch (compare_op_) {
            case CompareOp::kLessThan:
                cond_true = actual_int < cond_int;
                break;
            case CompareOp::kGreaterThan:
                cond_true = actual_int > cond_int;
                break;
            case CompareOp::kLessEqual:
                cond_true = actual_int <= cond_int;
                break;
            case CompareOp::kGreaterEqual:
                cond_true = actual_int >= cond_int;
                break;
            default:
                break;
        }

        if (DebugLogger::is_debug_enabled()) {
            DebugLogger::debug(
                "Condition comparison: " + std::to_string(actual_int) +
                compare_op_str(compare_op_) + std::to_string(cond_int) +
                " === " + (cond_true ? "true" : "false"));
        }
        return cond_true;
    }

    // Equality / inequality: compare JSON-encoded values.
    nlohmann::json cond_value_json;
    nlohmann::json actual_value_json;
    try {
        cond_value_json = nlohmann::json::parse(cond_value_);
        actual_value_json = nlohmann::json::parse(actual_value_str);
    } catch (const nlohmann::json::parse_error&) {
        cond_value_json = cond_value_;
        actual_value_json = actual_value_str;
    }

    std::string cond_value_encoded = cond_value_json.dump();
    std::string actual_value_encoded = actual_value_json.dump();
    bool value_matches = (actual_value_encoded == cond_value_encoded);
    bool cond_true =
        (compare_op_ == CompareOp::kNotEqual) ? !value_matches : value_matches;

    if (DebugLogger::is_debug_enabled()) {
        DebugLogger::debug("Condition comparison: (" + actual_value_encoded +
                           ")" + define_name_ + compare_op_str(compare_op_) +
                           cond_value_encoded +
                           " === " + (cond_true ? "true" : "false"));
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

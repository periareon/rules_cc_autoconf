#include "autoconf/private/checker/checker.h"

#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/config.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

int Checker::run_checks_by_define(
    const std::filesystem::path& config_path,
    const std::vector<std::string>& check_defines,
    const std::filesystem::path& results_path,
    const std::vector<std::filesystem::path>& required_results) {
    try {
        std::unique_ptr<Config> config = Config::from_file(config_path);

        // Load results from required check files (passed via --required
        // arguments) Each file contains JSON with check results:
        // {"DEFINE_NAME": {"success": bool, "value": str, "type": str}, ...} We merge all
        // results into a single map for efficient lookup during requirement
        // validation. We store both success status and value for value-based
        // requirement checks (e.g., "FOO=1").
        std::map<std::string, CheckResult> other_results{};
        for (const std::filesystem::path& result_file_path : required_results) {
            if (result_file_path.empty()) {
                throw std::runtime_error("Results path cannot be empty");
            }

            if (!std::filesystem::exists(result_file_path)) {
                throw std::runtime_error("Results path does not exist: " + result_file_path.string());
            }

            std::ifstream results_file(result_file_path);
            if (results_file.is_open()) {
                nlohmann::json results_json{};
                results_file >> results_json;
                for (nlohmann::json::iterator it = results_json.begin();
                        it != results_json.end(); ++it) {
                    const std::string& key = it.key();
                    const nlohmann::json& json_value = it.value();
                    std::optional<CheckResult> result = CheckResult::from_json(key, &json_value);
                    if (!result.has_value()) {
                        throw std::runtime_error("Failed to parse CheckResult: " + result_file_path.string());
                    }
                    other_results.emplace(key, *result);
                }
                results_file.close();
            }
        }

        CheckRunner runner(*config);

        // Extract AC_DEFINE defines from required checks to include in compilation tests
        // This matches GNU Autoconf behavior where AC_USE_SYSTEM_EXTENSIONS affects
        // all subsequent compilation tests
        std::map<std::string, std::string> required_defines_map;
        for (const auto& [define_name, info] : other_results) {
            // Include defines from successful AC_DEFINE checks (check define flag)
            // Exclude subst, m4_define, and other non-compile-time defines
            // These defines (like _GNU_SOURCE, _DARWIN_C_SOURCE) need to be available
            // during compilation tests, not just in config.h
            if (info.is_define && info.success && !info.value.empty()) {
                required_defines_map[define_name] = info.value;
            }
        }
        runner.set_required_defines(required_defines_map);
        nlohmann::json j = nlohmann::json::object();

        // Build a map of define names to check indices for fast lookup
        std::map<std::string, size_t> define_to_index;
        for (size_t i = 0; i < config->checks.size(); ++i) {
            define_to_index[config->checks[i].define()] = i;
        }

        for (const std::string& define : check_defines) {
            std::map<std::string, size_t>::iterator it =
                define_to_index.find(define);
            if (it == define_to_index.end()) {
                throw std::runtime_error("No check found with define '" +
                                         define + "'");
            }

            const Check& check = config->checks[it->second];

            // Check if all required defines are successful
            // Requirements can be:
            //   - "DEFINE_NAME" - check that DEFINE_NAME succeeded
            //   - "!DEFINE_NAME" - check that DEFINE_NAME failed
            //   - "DEFINE_NAME==value" - check that DEFINE_NAME succeeded AND
            //     has the specified value (exact match)
            //   - "DEFINE_NAME!=value" - check that DEFINE_NAME succeeded AND
            //     does NOT have the specified value
            //   - "DEFINE_NAME=value" - legacy syntax, same as ==
            //
            // Special case: For conditional checks (with condition field),
            // the condition itself is in requires just to ensure ordering,
            // but we don't require it to succeed - we just need its result
            // to pick the right value.
            bool requirements_met = true;

            // Get the condition name if this is a conditional check
            std::string condition_name;
            if (check.condition().has_value()) {
                condition_name = *check.condition();
            }

            if (check.required_defines().has_value()) {
                for (const std::string& req : *check.required_defines()) {
                    std::string req_define = req;
                    std::string req_value {};
                    bool negated = false;
                    bool has_value_requirement = false;
                    bool value_negated = false;

                    // Check for negation prefix (e.g., "!FOO")
                    if (!req_define.empty() && req_define[0] == '!') {
                        negated = true;
                        req_define = req_define.substr(1);
                    }

                    // Check for != operator first (e.g., "FOO!=1")
                    size_t neq_pos = req_define.find("!=");
                    if (neq_pos != std::string::npos) {
                        req_value = req_define.substr(neq_pos + 2);
                        req_define = req_define.substr(0, neq_pos);
                        has_value_requirement = true;
                        value_negated = true;
                    } else {
                        // Check for == operator (e.g., "FOO==1")
                        size_t eq2_pos = req_define.find("==");
                        if (eq2_pos != std::string::npos) {
                            req_value = req_define.substr(eq2_pos + 2);
                            req_define = req_define.substr(0, eq2_pos);
                            has_value_requirement = true;
                        } else {
                            // Check for legacy = operator (e.g., "FOO=1")
                            size_t eq_pos = req_define.find('=');
                            if (eq_pos != std::string::npos) {
                                req_value = req_define.substr(eq_pos + 1);
                                req_define = req_define.substr(0, eq_pos);
                                has_value_requirement = true;
                            }
                        }
                    }

                    std::map<std::string, CheckResult>::const_iterator
                        req_it = other_results.find(req_define);

                    if (req_it == other_results.end()) {
                        requirements_met = false;
                        DebugLogger::warn("Check '" + define + "' requires '" +
                                          req_define +
                                          "' which was not found, skipping");
                        break;
                    }

                    // Handle negated success check (e.g., "!FOO")
                    if (negated) {
                        if (req_it->second.success) {
                            requirements_met = false;
                            DebugLogger::warn(
                                "Check '" + define + "' requires '!" +
                                req_define +
                                "' (failure) but it succeeded, skipping");
                            break;
                        }
                        // Negated check passed - the required check failed
                        continue;
                    }

                    // For non-negated checks, require success
                    if (!req_it->second.success) {
                        requirements_met = false;
                        DebugLogger::warn(
                            "Check '" + define + "' requires '" + req_define +
                            "' which is not successful, skipping");
                        break;
                    }

                    // Handle value requirements
                    if (has_value_requirement) {
                        bool value_matches =
                            (req_it->second.value == req_value);
                        if (value_negated) {
                            // FOO!=val - require value does NOT match
                            if (value_matches) {
                                requirements_met = false;
                                DebugLogger::warn(
                                    "Check '" + define + "' requires '" +
                                    req_define + "!=" + req_value +
                                    "' but it has that value, skipping");
                                break;
                            }
                        } else {
                            // FOO==val or FOO=val - require value matches
                            if (!value_matches) {
                                requirements_met = false;
                                DebugLogger::warn(
                                    "Check '" + define + "' requires '" +
                                    req_define + "==" + req_value +
                                    "' but it has value '" +
                                    req_it->second.value + "', skipping");
                                break;
                            }
                        }
                    }
                }
            }

            CheckResult result(check.define(), "0", false);
            if (requirements_met) {
                // Handle conditional subst/define checks
                if (check.condition().has_value()) {
                    // Parse condition - may be "DEFINE_NAME", "DEFINE_NAME==value",
                    // or "DEFINE_NAME!=value"
                    std::string cond_expr = *check.condition();
                    std::string cond_define = cond_expr;
                    std::string cond_value;
                    bool has_value_compare = false;
                    bool value_negated = false;

                    // Check for != operator first (e.g., "FOO!=1")
                    size_t neq_pos = cond_expr.find("!=");
                    if (neq_pos != std::string::npos) {
                        cond_value = cond_expr.substr(neq_pos + 2);
                        cond_define = cond_expr.substr(0, neq_pos);
                        has_value_compare = true;
                        value_negated = true;
                    } else {
                        // Check for == operator (e.g., "FOO==1")
                        size_t eq2_pos = cond_expr.find("==");
                        if (eq2_pos != std::string::npos) {
                            cond_value = cond_expr.substr(eq2_pos + 2);
                            cond_define = cond_expr.substr(0, eq2_pos);
                            has_value_compare = true;
                        } else {
                            // Check for legacy = operator (e.g., "FOO=1")
                            size_t eq_pos = cond_expr.find('=');
                            if (eq_pos != std::string::npos) {
                                cond_value = cond_expr.substr(eq_pos + 1);
                                cond_define = cond_expr.substr(0, eq_pos);
                                has_value_compare = true;
                            }
                        }
                    }

                    // Look up the condition's result
                    std::map<std::string, CheckResult>::const_iterator
                        cond_it = other_results.find(cond_define);

                    if (cond_it != other_results.end()) {
                        // Debug log the condition with resolved value
                        if (DebugLogger::is_debug_enabled()) {
                            std::string debug_msg {};
                            if (has_value_compare) {
                                // Format: FOO(`value`)==0 or FOO(`value`)!=0
                                std::string operator_str = value_negated ? "!=" : "==";
                                debug_msg = cond_define + "(`" + cond_it->second.value + "`)" + operator_str + cond_value;
                            } else {
                                // Format: FOO(`value`)
                                debug_msg = cond_define + "(`" + cond_it->second.value + "`)";
                            }
                            DebugLogger::debug("Evaluating condition: " + debug_msg);
                        }

                        // Evaluate condition
                        bool cond_true = false;
                        if (has_value_compare) {
                            // Value comparison: check if value matches
                            bool value_matches = (cond_it->second.value == cond_value);
                            if (value_negated) {
                                cond_true = !value_matches;
                            } else {
                                cond_true = value_matches;
                            }
                        } else {
                            // Simple condition: check if check succeeded with a
                            // truthy value (non-empty, non-zero)
                            cond_true = cond_it->second.success &&
                                        !cond_it->second.value.empty() &&
                                        cond_it->second.value != "0";
                        }

                        std::string value;
                        if (cond_true) {
                            // Use if_true value (define_value)
                            value = check.define_value().value_or("");
                        } else {
                            // Use if_false value (define_value_fail)
                            value = check.define_value_fail().value_or("");
                        }

                        // If value is empty, check if it should create a define
                        // For AC_DEFINE (kDefine type) with empty value (if_true="" or if_false=""), we want "#define NAME /**/"
                        // For AC_DEFINE_UNQUOTED (kDefineUnquoted type) with empty value, we want "#define NAME " (trailing space)
                        // For AC_DEFINE with if_false=None, we don't want to define it
                        // Only kDefine and kDefineUnquoted types create defines with empty values
                        bool should_create_define = false;
                        if (value.empty()) {
                            // If we're using if_true and it's empty, or if we're using if_false and it's empty,
                            // that means the user explicitly set an empty value, so we should create a define
                            // The key is: if define_value or define_value_fail is set to empty string (has_value() && empty()),
                            // that means the user explicitly wanted an empty value, not that it wasn't provided
                            if (cond_true) {
                                // Using if_true branch - check if if_true was explicitly set to empty string
                                if (check.define_value().has_value() && check.define_value()->empty()) {
                                    // if_true was explicitly set to empty string
                                    should_create_define = (check.type() == CheckType::kDefine || check.type() == CheckType::kDefineUnquoted);
                                }
                            } else {
                                // Using if_false branch - check if if_false was explicitly set to empty string
                                if (check.define_value_fail().has_value() && check.define_value_fail()->empty()) {
                                    // if_false was explicitly set to empty string
                                    should_create_define = (check.type() == CheckType::kDefine || check.type() == CheckType::kDefineUnquoted);
                                }
                            }
                            // Otherwise, if_false was not provided (None), so don't create a define
                        }
                        
                        if (!value.empty() || should_create_define) {
                            result = CheckResult(check.define(), value, true,
                                                 check_type_is_define(check.type()),
                                                 check_type_is_subst(check.type()),
                                                 check.type());
                        } else {
                            // Mark as not successful so it won't be output (if_false=None case)
                            result = CheckResult(check.define(), "", false,
                                                 check_type_is_define(check.type()),
                                                 check_type_is_subst(check.type()),
                                                 check.type());
                        }
                    } else {
                        DebugLogger::warn("Conditional check '" + define +
                                          "' references '" + cond_define +
                                          "' which was not found");
                        result = CheckResult(check.define(), "", false,
                                             check_type_is_define(check.type()),
                                             check_type_is_subst(check.type()),
                                             check.type());
                    }
                } else {
                    result = runner.run_check(check);
                }
            }

            j[result.define] = {
                {"value", result.value},
                {"success", result.success},
                {"is_define", result.is_define},
                {"is_subst", result.is_subst},
                {"type", check_type_to_string(result.type)},
            };
        }

        std::ofstream results_file(results_path);
        if (!results_file.is_open()) {
            std::cerr << "Error: Failed to open results file: " << results_path
                      << std::endl;
            return 1;
        }
        results_file << j.dump(4) << std::endl;
        results_file.close();

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

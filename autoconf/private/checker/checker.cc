#include "autoconf/private/checker/checker.h"

#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <unordered_map>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/condition_evaluator.h"
#include "autoconf/private/checker/config.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

namespace {

/**
 * @brief Lookup structure for check results using vector + hash map.
 *
 * Stores results in a vector (single source of truth) and indexes by
 * lookup names (cache variable, define, subst) using a hash map.
 */
class ResultLookup {
   public:
    /**
     * @brief Add a name->file mapping.
     * @param lookup_name The name to index (cache variable, define, or subst
     * name).
     * @param file_path Path to the JSON file containing the result.
     * @throws std::runtime_error if the name is already mapped to a different
     * file.
     */
    void add_mapping(const std::string& lookup_name,
                     const std::filesystem::path& file_path) {
        // Check for duplicate name (strict - any duplicate is an error)
        std::unordered_map<std::string, size_t>::iterator it =
            name_to_index_.find(lookup_name);
        if (it != name_to_index_.end()) {
            // Duplicate name detected
            size_t existing_idx = it->second;
            std::filesystem::path existing_file = file_to_path_[existing_idx];
            if (existing_file != file_path) {
                throw std::runtime_error(
                    "Duplicate --dep argument for name '" + lookup_name +
                    "':\n"
                    "  Name '" +
                    lookup_name +
                    "' was already mapped to file:\n"
                    "    " +
                    existing_file.string() +
                    "\n"
                    "  Attempted to map to different file:\n"
                    "    " +
                    file_path.string() +
                    "\n"
                    "  This indicates a bug in Starlark code - it should "
                    "deduplicate before calling C++.");
            }
            // Same name, same file - idempotent, skip
            return;
        }

        // Load result from file (or get existing index if file already loaded)
        size_t idx = load_or_get_index(file_path);

        // Index by lookup name
        name_to_index_[lookup_name] = idx;
    }

    /**
     * @brief Find a result by lookup name.
     * @param name The lookup name (cache variable, define, or subst name).
     * @return Pointer to CheckResult, or nullptr if not found.
     */
    const CheckResult* find(const std::string& name) const {
        std::unordered_map<std::string, size_t>::const_iterator it =
            name_to_index_.find(name);
        if (it == name_to_index_.end()) {
            return nullptr;
        }
        return &results_[it->second];
    }

    /**
     * @brief Get all results as a map (for backward compatibility).
     * @return Map of lookup names to CheckResult references.
     */
    std::map<std::string, CheckResult> to_map() const {
        std::map<std::string, CheckResult> result_map;
        for (const auto& [name, idx] : name_to_index_) {
            result_map.emplace(name, results_[idx]);
        }
        return result_map;
    }

   private:
    std::vector<CheckResult> results_;  // Single source of truth
    std::unordered_map<std::string, size_t>
        name_to_index_;  // Lookup name -> index
    std::unordered_map<std::filesystem::path, size_t>
        file_to_index_;  // File path -> index
    std::unordered_map<size_t, std::filesystem::path>
        file_to_path_;  // Index -> file path (for error messages)

    /**
     * @brief Load result from file or return existing index if already loaded.
     * @param file_path Path to JSON file containing the result.
     * @return Index of the result in results_ vector.
     */
    size_t load_or_get_index(const std::filesystem::path& file_path) {
        // Check if file already loaded
        std::unordered_map<std::filesystem::path, size_t>::iterator file_it =
            file_to_index_.find(file_path);
        if (file_it != file_to_index_.end()) {
            return file_it->second;  // Already loaded
        }

        // Load from file
        if (!std::filesystem::exists(file_path)) {
            throw std::runtime_error("Dep results file does not exist: " +
                                     file_path.string());
        }

        std::ifstream results_file(file_path);
        if (!results_file.is_open()) {
            throw std::runtime_error("Failed to open dep results file: " +
                                     file_path.string());
        }

        nlohmann::json results_json;
        results_file >> results_json;
        results_file.close();

        // Parse result from JSON (expect single result per file)
        if (results_json.empty() || !results_json.is_object()) {
            throw std::runtime_error("Dep results file is empty or invalid: " +
                                     file_path.string());
        }

        // Get the first (and should be only) result from the JSON
        nlohmann::json::iterator it = results_json.begin();
        const std::string& key = it.key();
        const nlohmann::json& json_value = it.value();

        std::optional<CheckResult> result =
            CheckResult::from_json(key, &json_value);
        if (!result.has_value()) {
            throw std::runtime_error("Failed to parse CheckResult from file: " +
                                     file_path.string());
        }

        // Add to results vector
        size_t idx = results_.size();
        results_.push_back(*result);
        file_to_index_[file_path] = idx;
        file_to_path_[idx] = file_path;

        return idx;
    }
};

}  // namespace

int Checker::run_check_from_file(const std::filesystem::path& check_path,
                                 const std::filesystem::path& config_path,
                                 const std::filesystem::path& results_path,
                                 const std::vector<DepMapping>& dep_mappings) {
    try {
        // Load config for compiler info only
        std::unique_ptr<Config> config = Config::from_file(config_path);

        // Load the check from JSON file
        std::ifstream check_file(check_path);
        if (!check_file.is_open()) {
            throw std::runtime_error("Failed to open check file: " +
                                     check_path.string());
        }

        nlohmann::json check_json;
        check_file >> check_json;
        check_file.close();

        std::optional<Check> check_opt = Check::from_json(&check_json);
        if (!check_opt.has_value()) {
            throw std::runtime_error("Failed to parse check from file: " +
                                     check_path.string());
        }
        const Check& check = *check_opt;

        // Load results from dependent check files using explicit name->file
        // mappings
        ResultLookup result_lookup;

        // Handle legacy format (empty lookup_name) - extract names from JSON
        for (const DepMapping& mapping : dep_mappings) {
            if (mapping.lookup_name.empty()) {
                // Legacy format: --dep=file_path (no name specified)
                // Extract all names (cache, define, subst) from the JSON file
                if (!std::filesystem::exists(mapping.file_path)) {
                    throw std::runtime_error(
                        "Dep results file does not exist: " +
                        mapping.file_path.string());
                }

                std::ifstream results_file(mapping.file_path);
                if (!results_file.is_open()) {
                    throw std::runtime_error(
                        "Failed to open dep results file: " +
                        mapping.file_path.string());
                }

                nlohmann::json results_json;
                results_file >> results_json;
                results_file.close();

                for (nlohmann::json::iterator it = results_json.begin();
                     it != results_json.end(); ++it) {
                    const std::string& key = it.key();
                    const nlohmann::json& json_value = it.value();
                    std::optional<CheckResult> result =
                        CheckResult::from_json(key, &json_value);
                    if (!result.has_value()) {
                        throw std::runtime_error(
                            "Failed to parse CheckResult: " +
                            mapping.file_path.string());
                    }

                    // Index by all possible names (legacy behavior)
                    result_lookup.add_mapping(result->name, mapping.file_path);
                    if (result->define.has_value()) {
                        result_lookup.add_mapping(*result->define,
                                                  mapping.file_path);
                    }
                    if (result->subst.has_value()) {
                        result_lookup.add_mapping(*result->subst,
                                                  mapping.file_path);
                    }
                }
            } else {
                // New format: --dep=name=file_path (explicit name mapping)
                result_lookup.add_mapping(mapping.lookup_name,
                                          mapping.file_path);
            }
        }

        // Convert to map for backward compatibility with existing code
        std::map<std::string, CheckResult> dep_results_map =
            result_lookup.to_map();

        // Debug: log what's in the map
        if (DebugLogger::is_debug_enabled()) {
            DebugLogger::debug("Dep results map contains " +
                               std::to_string(dep_results_map.size()) +
                               " entries:");
            for (const auto& [key, result] : dep_results_map) {
                std::string define_str =
                    result.define.has_value() ? *result.define : "(none)";
                DebugLogger::debug("  Key: '" + key + "', define: '" +
                                   define_str + "', value: '" +
                                   result.value.value_or("") + "'");
            }
        }

        CheckRunner runner(*config);

        // Extract AC_DEFINE defines from dependent checks to include in
        // compilation tests Since dep_results_map now has multiple entries per
        // result (by name, define, subst), we need to process each unique
        // result only once
        std::map<std::string, std::string> compile_defines_map;
        std::set<std::string> processed_results;
        for (const auto& [key, info] : dep_results_map) {
            // Only process each result once (use cache variable name as unique
            // identifier)
            if (processed_results.find(info.name) != processed_results.end()) {
                continue;
            }
            processed_results.insert(info.name);

            if (info.is_define && info.success && info.value.has_value() &&
                !info.value->empty()) {
                // Use define name if available, otherwise use cache variable
                // name
                std::string define_name =
                    info.define.has_value() ? *info.define : info.name;
                compile_defines_map[define_name] = *info.value;
            }
        }
        runner.set_required_defines(compile_defines_map);
        runner.set_dep_results(dep_results_map);

        // Create a combined results map that includes both dependency results
        // and results from the current target (as they're processed)
        std::map<std::string, CheckResult> all_results_map = dep_results_map;

        // Check if all required defines are successful
        bool requirements_met = true;

        // Get the condition name if this is a conditional check
        std::string condition_name;
        if (check.condition().has_value()) {
            condition_name = *check.condition();
        }

        if (check.required_defines().has_value()) {
            for (const std::string& req : *check.required_defines()) {
                // Check for negation prefix (e.g., "!FOO") - handle separately
                bool negated = ConditionEvaluator::has_negation_prefix(req);
                std::string req_expr =
                    negated ? ConditionEvaluator::strip_negation_prefix(req)
                            : req;

                // Use ConditionEvaluator for parsing and value comparison
                ConditionEvaluator evaluator(req_expr);

                // Look up the requirement's result from all available results
                // If lookup fails, this is a configuration error (missing
                // dependency) - throw exception
                std::string check_name =
                    check.define().has_value() ? *check.define() : check.name();
                const CheckResult* req_result_ptr;
                try {
                    req_result_ptr =
                        evaluator.find_condition_result(all_results_map);
                } catch (const std::exception& ex) {
                    // Wrap with check context for better error message
                    throw std::runtime_error(
                        "Check '" + check_name + "' requires '" +
                        evaluator.define_name() +
                        "' but dependency lookup failed: " + ex.what());
                }

                // Handle negated success check (e.g., "!FOO")
                if (negated) {
                    if (req_result_ptr->success) {
                        requirements_met = false;
                        std::string check_name = check.define().has_value()
                                                     ? *check.define()
                                                     : check.name();
                        DebugLogger::warn(
                            "Check '" + check_name + "' requires '!" +
                            evaluator.define_name() +
                            "' (failure) but it succeeded, skipping");
                        break;
                    }
                    continue;
                }

                // For non-negated checks, require success
                if (!req_result_ptr->success) {
                    requirements_met = false;
                    std::string check_name = check.define().has_value()
                                                 ? *check.define()
                                                 : check.name();
                    DebugLogger::warn("Check '" + check_name + "' requires '" +
                                      evaluator.define_name() +
                                      "' which is not successful, skipping");
                    break;
                }

                // Handle value requirements using the evaluator
                if (evaluator.has_value_compare()) {
                    bool condition_satisfied =
                        evaluator.evaluate(req_result_ptr);
                    if (!condition_satisfied) {
                        requirements_met = false;
                        std::string check_name = check.define().has_value()
                                                     ? *check.define()
                                                     : check.name();
                        std::string operator_str =
                            evaluator.is_negated() ? "!=" : "==";
                        DebugLogger::warn(
                            "Check '" + check_name + "' requires '" +
                            evaluator.define_name() + operator_str +
                            evaluator.comparison_value() +
                            "' but condition is not satisfied (value is '" +
                            req_result_ptr->value.value_or("") +
                            "'), skipping");
                        break;
                    }
                }
            }
        }

        std::string define_name =
            check.define().has_value() ? *check.define() : check.name();
        // When requires fails, create result with nullopt value (not "0") so
        // resolver produces /* #undef */
        CheckResult result(define_name, std::nullopt, false);
        if (requirements_met) {
            if (check.condition().has_value()) {
                // Handle conditional subst/define checks
                ConditionEvaluator evaluator(*check.condition());
                bool cond_true = evaluator.compute(all_results_map);

                std::optional<std::string> value;
                if (cond_true) {
                    // For conditional checks, define_value is always set in
                    // JSON (even if None/null) This matches behavior of direct
                    // value parameter - if_true behaves like value
                    if (check.define_value().has_value()) {
                        value = *check.define_value();
                    } else {
                        // Field exists in JSON but is null/None - use empty
                        // string to create /**/ This matches check_define
                        // behavior for direct AC_DEFINE with value=None
                        value = std::optional<std::string>("");
                    }
                } else {
                    // When condition fails: if define_value_fail has a value
                    // (including ""), create define with it. If
                    // define_value_fail is null (if_false=None meaning "don't
                    // define when fail"), don't create define.
                    if (check.define_value_fail().has_value()) {
                        value = *check.define_value_fail();
                    } else {
                        // define_value_fail is null (if_false=None) -> don't
                        // define when condition fails (/* #undef */)
                        value = std::nullopt;
                    }
                }

                bool should_create_define = false;
                if (value.has_value() && value->empty()) {
                    should_create_define = (check.type() == CheckType::kDefine);
                }

                std::string result_name = check.name();
                if (value.has_value() &&
                    (!value->empty() || should_create_define)) {
                    result = CheckResult(result_name, value, cond_true,
                                         check_type_is_define(check.type()),
                                         check.subst().has_value(),
                                         check.type(), check.define(),
                                         check.subst(), check.unquote());
                } else {
                    result = CheckResult(result_name, std::nullopt, false,
                                         check_type_is_define(check.type()),
                                         check.subst().has_value(),
                                         check.type(), check.define(),
                                         check.subst(), check.unquote());
                }
            } else {
                result = runner.run_check(check);
            }
        }

        nlohmann::json j = nlohmann::json::object();
        // Parse the value as JSON to preserve type information when writing
        // result.value is a JSON-encoded string, so we parse it to get the
        // actual JSON value
        nlohmann::json value_json;
        if (result.value.has_value()) {
            if (result.value->empty()) {
                // Explicitly empty value: write as empty string in JSON
                value_json = "";
            } else {
                try {
                    value_json = nlohmann::json::parse(*result.value);
                } catch (const nlohmann::json::parse_error&) {
                    // If parsing fails, treat as plain string
                    value_json = *result.value;
                }
            }
        } else {
            // No value: write as null in JSON
            value_json = nullptr;
        }
        nlohmann::json result_json = {
            {"value", value_json},
            {"success", result.success},
            {"is_define", result.is_define},
            {"is_subst", result.is_subst},
            {"type", check_type_to_string(result.type)},
            {"unquote", result.unquote},
        };
        if (result.define.has_value()) {
            result_json["define"] = *result.define;
        }
        if (result.subst.has_value()) {
            result_json["subst"] = *result.subst;
        }
        j[result.name] = result_json;

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

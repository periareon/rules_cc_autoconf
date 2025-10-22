#include "autoconf/private/checker/checker.h"

#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>

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
        // {"DEFINE_NAME": {"success": bool, "value": str}, ...} We merge all
        // results into a single map for efficient lookup during requirement
        // validation. We store both success status and value for value-based
        // requirement checks (e.g., "FOO=1").
        struct CheckResultInfo {
            bool success;
            std::string value;
        };
        std::map<std::string, CheckResultInfo> other_results{};
        for (const std::filesystem::path& result_file_path : required_results) {
            if (!result_file_path.empty() &&
                std::filesystem::exists(result_file_path)) {
                std::ifstream results_file(result_file_path);
                if (results_file.is_open()) {
                    nlohmann::json results_json{};
                    results_file >> results_json;
                    for (nlohmann::json::iterator it = results_json.begin();
                         it != results_json.end(); ++it) {
                        const std::string& key = it.key();
                        const nlohmann::json& json_value = it.value();
                        if (json_value.is_object() &&
                            json_value.contains("success") &&
                            json_value["success"].is_boolean()) {
                            CheckResultInfo info;
                            info.success = json_value["success"].get<bool>();
                            info.value =
                                json_value.contains("value") &&
                                        json_value["value"].is_string()
                                    ? json_value["value"].get<std::string>()
                                    : "";
                            other_results[key] = info;
                        }
                    }
                    results_file.close();
                }
            }
        }

        CheckRunner runner(*config);
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
            //   - "DEFINE_NAME=value" - check that DEFINE_NAME succeeded AND
            //     has the specified value
            bool requirements_met = true;
            if (check.required_defines().has_value()) {
                for (const std::string& req : *check.required_defines()) {
                    std::string req_define = req;
                    std::string req_value;
                    bool has_value_requirement = false;

                    // Check if requirement has a value constraint (e.g.,
                    // "FOO=1")
                    size_t eq_pos = req.find('=');
                    if (eq_pos != std::string::npos) {
                        req_define = req.substr(0, eq_pos);
                        req_value = req.substr(eq_pos + 1);
                        has_value_requirement = true;
                    }

                    std::map<std::string, CheckResultInfo>::const_iterator
                        req_it = other_results.find(req_define);

                    if (req_it == other_results.end()) {
                        requirements_met = false;
                        DebugLogger::warn("Check '" + define + "' requires '" +
                                          req_define +
                                          "' which was not found, skipping");
                        break;
                    }

                    if (!req_it->second.success) {
                        requirements_met = false;
                        DebugLogger::warn(
                            "Check '" + define + "' requires '" + req_define +
                            "' which is not successful, skipping");
                        break;
                    }

                    if (has_value_requirement &&
                        req_it->second.value != req_value) {
                        requirements_met = false;
                        DebugLogger::warn("Check '" + define + "' requires '" +
                                          req_define + "=" + req_value +
                                          "' but it has value '" +
                                          req_it->second.value + "', skipping");
                        break;
                    }
                }
            }

            CheckResult result(check.define(), "0", false);
            if (requirements_met) {
                result = runner.run_check(check);
            }

            j[result.define] = {
                {"value", result.value},
                {"success", result.success},
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

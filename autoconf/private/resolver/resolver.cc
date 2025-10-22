#include "autoconf/private/resolver/resolver.h"

#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <unordered_map>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/check_runner.h"
#include "autoconf/private/checker/checker.h"
#include "autoconf/private/checker/config.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/json/json.h"
#include "autoconf/private/resolver/source_generator.h"

namespace rules_cc_autoconf {

namespace {

// Helper function to load results from JSON file
std::vector<CheckResult> load_results_from_file(
    const std::filesystem::path& path) {
    std::vector<CheckResult> loaded_results;
    std::ifstream results_file(path);
    if (!results_file.is_open()) {
        throw std::runtime_error("Failed to open results file: " +
                                 path.string());
    }

    nlohmann::json j;
    results_file >> j;
    results_file.close();

    // Handle null or empty JSON - treat as empty results
    if (j.is_null() || !j.is_object()) {
        j = nlohmann::json::object();
    }

    for (nlohmann::json::iterator it = j.begin(); it != j.end(); ++it) {
        const std::string define = it.key();
        const nlohmann::json& val = it.value();

        std::string value;
        nlohmann::json::const_iterator value_it = val.find("value");
        if (value_it != val.end() && !value_it->is_null()) {
            value = value_it->get<std::string>();
        }

        bool success = false;
        nlohmann::json::const_iterator success_it = val.find("success");
        if (success_it != val.end()) {
            success = success_it->get<bool>();
        }

        loaded_results.emplace_back(define, value, success);
    }

    return loaded_results;
}

// Helper function to create a map of existing defines for quick lookup
std::map<std::string, bool> create_results_map(
    const std::vector<CheckResult>& results_vec) {
    std::map<std::string, bool> results_map;
    for (const CheckResult& r : results_vec) {
        results_map[r.define] = true;
    }
    return results_map;
}

}  // namespace

int Resolver::resolve_and_generate(
    const std::vector<std::filesystem::path>& results_paths,
    const std::filesystem::path& package_info_path,
    const std::optional<std::filesystem::path>& template_path,
    const std::optional<std::filesystem::path>& config_path,
    const std::filesystem::path& output_path,
    const std::optional<std::filesystem::path>& output_results_path) {
    try {
        // Load and merge all results (preserve order while deduplicating by
        // define name, keeping first occurrence to preserve autoconf results
        // over template checks)
        std::unordered_map<std::string, CheckResult> results_map{};
        std::vector<std::string>
            define_order{};  // Preserve order of first occurrence
        for (const auto& results_path : results_paths) {
            if (!std::filesystem::exists(results_path)) {
                std::cerr << "Warning: Results file does not exist: "
                          << results_path << ", skipping" << std::endl;
                continue;
            }
            std::vector<CheckResult> loaded =
                load_results_from_file(results_path);
            // Only add results that haven't been seen yet, or verify they match
            for (const CheckResult& result : loaded) {
                std::unordered_map<std::string, CheckResult>::const_iterator
                    existing_it = results_map.find(result.define);
                if (existing_it == results_map.end()) {
                    results_map.emplace(result.define, result);
                    define_order.push_back(result.define);
                } else {
                    // Duplicate define found - check if values match
                    const CheckResult& existing = existing_it->second;
                    if (existing.success != result.success ||
                        existing.value != result.value) {
                        std::cerr << "Error: Duplicate define '"
                                  << result.define
                                  << "' with conflicting values:\n";
                        std::cerr << "  First:  success="
                                  << (existing.success ? "true" : "false")
                                  << ", value=\"" << existing.value << "\"\n";
                        std::cerr << "  Second: success="
                                  << (result.success ? "true" : "false")
                                  << ", value=\"" << result.value << "\""
                                  << std::endl;
                        return 1;
                    }
                    // Values match, silently ignore duplicate
                }
            }
        }
        // Convert map to vector preserving order
        std::vector<CheckResult> results;
        results.reserve(define_order.size());
        for (const std::string& define : define_order) {
            std::unordered_map<std::string, CheckResult>::const_iterator it =
                results_map.find(define);
            if (it != results_map.end()) {
                results.push_back(it->second);
            }
        }

        // Load package info
        std::ifstream pkg_file(package_info_path);
        if (!pkg_file.is_open()) {
            std::cerr << "Error: Failed to open package info file: "
                      << package_info_path << std::endl;
            return 1;
        }

        nlohmann::json pkg_json;
        pkg_file >> pkg_json;
        pkg_file.close();

        // Create config from package info
        std::unique_ptr<Config> config = std::make_unique<Config>();
        nlohmann::json::const_iterator name_it = pkg_json.find("name");
        if (name_it != pkg_json.end() && !name_it->is_null()) {
            config->package_name = name_it->get<std::string>();
        }

        nlohmann::json::const_iterator version_it = pkg_json.find("version");
        if (version_it != pkg_json.end() && !version_it->is_null()) {
            config->package_version = version_it->get<std::string>();
        }

        // If template is provided and config is provided, run additional checks
        if (template_path.has_value() && config_path.has_value()) {
            // Load template content
            std::ifstream template_file(*template_path);
            if (!template_file.is_open()) {
                std::cerr << "Error: Failed to open template file: "
                          << *template_path << std::endl;
                return 1;
            }
            std::stringstream buffer;
            buffer << template_file.rdbuf();
            std::string template_content = buffer.str();
            template_file.close();

            // Load full config (including toolchain info)
            std::unique_ptr<Config> full_config =
                Config::from_file(*config_path);

            // Update package info from package_info_path (override config
            // file's package info)
            if (name_it != pkg_json.end() && !name_it->is_null()) {
                full_config->package_name = name_it->get<std::string>();
            }
            if (version_it != pkg_json.end() && !version_it->is_null()) {
                full_config->package_version = version_it->get<std::string>();
            }

            // Add automatic header checks for defines found in template but not
            // in config
            *full_config = full_config->with_template_checks(template_content);

            // Filter out checks that already have results
            std::map<std::string, bool> existing_results =
                create_results_map(results);
            std::vector<Check> checks_to_run;
            for (const Check& check : full_config->checks) {
                // Only run checks for defines that don't already have results
                if (existing_results.find(check.define()) ==
                    existing_results.end()) {
                    checks_to_run.push_back(check);
                }
            }

            // Run only the new checks using checker library
            if (!checks_to_run.empty()) {
                Config temp_config = *full_config;
                temp_config.checks = checks_to_run;
                CheckRunner runner(temp_config);
                std::vector<CheckResult> new_results = runner.run_all_checks();

                // Merge new results with existing results (only add if not
                // already present) Preserve order: existing results first,
                // then new results
                std::unordered_map<std::string, CheckResult> results_map_merge;
                std::vector<std::string> define_order_merge;
                // Add existing results first (preserve their order)
                for (const CheckResult& r : results) {
                    results_map_merge.emplace(r.define, r);
                    define_order_merge.push_back(r.define);
                }
                // Add new results if not already present
                for (const CheckResult& new_result : new_results) {
                    // Check if this define already exists
                    std::unordered_map<std::string, CheckResult>::const_iterator
                        existing_it = results_map_merge.find(new_result.define);
                    if (existing_it == results_map_merge.end()) {
                        results_map_merge.emplace(new_result.define,
                                                  new_result);
                        define_order_merge.push_back(new_result.define);
                    } else {
                        // Duplicate define found - check if values match
                        const CheckResult& existing = existing_it->second;
                        if (existing.success != new_result.success ||
                            existing.value != new_result.value) {
                            std::cerr << "Error: Duplicate define '"
                                      << new_result.define
                                      << "' with conflicting values:\n";
                            std::cerr << "  First:  success="
                                      << (existing.success ? "true" : "false")
                                      << ", value=\"" << existing.value
                                      << "\"\n";
                            std::cerr << "  Second: success="
                                      << (new_result.success ? "true" : "false")
                                      << ", value=\"" << new_result.value
                                      << "\"" << std::endl;
                            return 1;
                        }
                        // Values match, silently ignore duplicate
                    }
                }
                // Convert back to vector preserving order
                results.clear();
                results.reserve(define_order_merge.size());
                for (const std::string& define : define_order_merge) {
                    std::unordered_map<std::string, CheckResult>::const_iterator
                        it = results_map_merge.find(define);
                    if (it != results_map_merge.end()) {
                        results.push_back(it->second);
                    }
                }
            }

            // Use full_config for header generation
            config = std::move(full_config);
        }

        // Log all check results in order
        if (DebugLogger::is_debug_enabled()) {
            for (const CheckResult& result : results) {
                std::string status = result.success ? "yes" : "no";
                DebugLogger::log("checking " + result.define + "... " + status);
            }
        }

        // Generate header
        SourceGenerator generator(*config, results);

        // Get template content
        std::string template_content{};
        if (template_path.has_value()) {
            std::ifstream template_file(*template_path);
            if (!template_file.is_open()) {
                std::cerr << "Error: Failed to open template file: "
                          << *template_path << std::endl;
                return 1;
            }
            std::stringstream buffer;
            buffer << template_file.rdbuf();
            template_content = buffer.str();
            template_file.close();
        } else {
            // Use default template if none was provided
            template_content = generator.generate_default_template();
        }

        generator.generate_config_header(output_path, template_content);

        // Optionally write merged results to output file
        if (output_results_path.has_value()) {
            nlohmann::json j = nlohmann::json::object();
            for (const CheckResult& r : results) {
                j[r.define] = {
                    {"value", r.value},
                    {"success", r.success},
                };
            }

            std::ofstream output_results_file(*output_results_path);
            if (!output_results_file.is_open()) {
                std::cerr << "Error: Failed to open output results file: "
                          << *output_results_path << std::endl;
                return 1;
            }
            output_results_file << j.dump(4) << std::endl;
            output_results_file.close();
        }

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

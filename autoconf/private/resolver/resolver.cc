#include "autoconf/private/resolver/resolver.h"

#include <algorithm>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/checker.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/resolver/source_generator.h"
#include "tools/json/json.h"

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

    // Use CheckResult::from_json() to parse each result
    for (nlohmann::json::iterator it = j.begin(); it != j.end(); ++it) {
        const std::string name = it.key();
        const nlohmann::json& json_value = it.value();

        std::optional<CheckResult> result =
            CheckResult::from_json(name, &json_value);
        if (!result.has_value()) {
            throw std::runtime_error("Failed to parse CheckResult from file: " +
                                     path.string());
        }

        loaded_results.push_back(*result);
    }

    return loaded_results;
}

}  // namespace

int Resolver::resolve_and_generate(
    const std::vector<std::filesystem::path>& cache_results_paths,
    const std::vector<std::filesystem::path>& define_results_paths,
    const std::vector<std::filesystem::path>& subst_results_paths,
    const std::filesystem::path& template_path,
    const std::filesystem::path& output_path,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions, Mode mode) {
    try {
        // Helper function to load and merge results from a list of paths
        std::function<std::vector<CheckResult>(
            const std::vector<std::filesystem::path>&)>
            load_and_merge_results = [](const std::vector<
                                         std::filesystem::path>& paths) {
                std::unordered_map<std::string, CheckResult> results_map{};
                std::vector<std::string>
                    order{};  // Preserve order of first occurrence
                for (const std::filesystem::path& results_path : paths) {
                    if (!std::filesystem::exists(results_path)) {
                        throw std::runtime_error(
                            "Results file does not exist: " +
                            results_path.string());
                    }
                    std::vector<CheckResult> loaded =
                        load_results_from_file(results_path);
                    // Only add results that haven't been seen yet, or verify
                    // they match
                    for (const CheckResult& result : loaded) {
                        std::unordered_map<std::string, CheckResult>::iterator
                            existing_it = results_map.find(result.name);
                        if (existing_it != results_map.end()) {
                            // Duplicate cache variable found - check if values
                            // match
                            const CheckResult& existing = existing_it->second;
                            if (existing.success != result.success ||
                                existing.value != result.value) {
                                std::cerr << "Error: Duplicate result '"
                                          << result.name
                                          << "' with conflicting values:\n";
                                std::cerr
                                    << "  First:  success="
                                    << (existing.success ? "true" : "false")
                                    << ", value=\""
                                    << existing.value.value_or("") << "\"\n";
                                std::cerr << "  Second: success="
                                          << (result.success ? "true" : "false")
                                          << ", value=\""
                                          << result.value.value_or("") << "\""
                                          << std::endl;
                                throw std::runtime_error(
                                    "Conflicting result values");
                            }
                            // Values match, silently ignore duplicate
                        } else {
                            results_map.emplace(result.name, result);
                            order.push_back(result.name);
                        }
                    }
                }
                // Convert map to vector preserving order
                std::vector<CheckResult> results;
                results.reserve(order.size());
                for (const std::string& name : order) {
                    std::unordered_map<std::string, CheckResult>::iterator it =
                        results_map.find(name);
                    if (it != results_map.end()) {
                        results.push_back(it->second);
                    }
                }
                return results;
            };

        // Load results from all three buckets
        // Note: Results are loaded from files, but we need to route them to the
        // correct bucket based on their is_define and is_subst flags. However,
        // since we're passing separate file lists for each bucket, we assume
        // files in cache_results_paths contain cache results, files in
        // define_results_paths contain define results, and files in
        // subst_results_paths contain subst results. This matches how
        // autoconf.bzl writes the files.
        std::vector<CheckResult> cache_results =
            load_and_merge_results(cache_results_paths);
        std::vector<CheckResult> define_results =
            load_and_merge_results(define_results_paths);
        std::vector<CheckResult> subst_results =
            load_and_merge_results(subst_results_paths);

        // Merge all results for logging and backward compatibility
        std::vector<CheckResult> all_results;
        all_results.reserve(cache_results.size() + define_results.size() +
                            subst_results.size());
        all_results.insert(all_results.end(), cache_results.begin(),
                           cache_results.end());
        all_results.insert(all_results.end(), define_results.begin(),
                           define_results.end());
        all_results.insert(all_results.end(), subst_results.begin(),
                           subst_results.end());

        // Log only define values (config.h defines), not cache or subst results
        if (DebugLogger::is_debug_enabled()) {
            std::vector<CheckResult> sorted_defines = define_results;
            std::sort(sorted_defines.begin(), sorted_defines.end(),
                      [](const CheckResult& a, const CheckResult& b) {
                          std::string a_define =
                              a.define.has_value() ? *a.define : a.name;
                          std::string b_define =
                              b.define.has_value() ? *b.define : b.name;
                          return a_define < b_define;
                      });
            for (const CheckResult& result : sorted_defines) {
                std::string status = result.success ? "yes" : "no";
                std::string define_name =
                    result.define.has_value() ? *result.define : result.name;
                DebugLogger::log("checking " + define_name + "... " + status);
            }
        }

        // Generate header with three separate buckets
        SourceGenerator generator(cache_results, define_results, subst_results,
                                  mode);

        std::string template_content{};
        std::ifstream template_file(template_path);
        if (!template_file.is_open()) {
            std::cerr << "Error: Failed to open template file: "
                      << template_path << std::endl;
            return 1;
        }
        std::stringstream buffer{};
        buffer << template_file.rdbuf();
        template_content = buffer.str();
        template_file.close();

        generator.generate_config_header(output_path, template_content, inlines,
                                         substitutions);

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

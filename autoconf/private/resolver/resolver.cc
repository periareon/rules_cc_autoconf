#include "autoconf/private/resolver/resolver.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <unordered_map>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
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

}  // namespace

int Resolver::resolve_and_generate(
    const std::vector<std::filesystem::path>& results_paths,
    const std::optional<std::filesystem::path>& template_path,
    const std::filesystem::path& output_path,
    const std::map<std::string, std::filesystem::path>& inlines) {
    try {
        // Load and merge all results (preserve order while deduplicating by
        // define name, keeping first occurrence to preserve autoconf results
        // over template checks)
        std::unordered_map<std::string, CheckResult> results_map{};
        std::vector<std::string>
            define_order{};  // Preserve order of first occurrence
        for (const std::filesystem::path& results_path : results_paths) {
            if (!std::filesystem::exists(results_path)) {
                throw std::runtime_error("Results file does not exist: " +
                                         results_path.string());
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

        std::unique_ptr<Config> config = std::make_unique<Config>();

        // Log all check results sorted by define name
        if (DebugLogger::is_debug_enabled()) {
            // Sort results by define name for readable debug output
            std::vector<CheckResult> sorted_results = results;
            std::sort(sorted_results.begin(), sorted_results.end(),
                      [](const CheckResult& a, const CheckResult& b) {
                          return a.define < b.define;
                      });
            for (const CheckResult& result : sorted_results) {
                std::string status = result.success ? "yes" : "no";
                DebugLogger::log("checking " + result.define + "... " + status);
            }
        }

        // Generate header
        SourceGenerator generator(results);

        std::string template_content{};
        std::ifstream template_file(*template_path);
        if (!template_file.is_open()) {
            std::cerr << "Error: Failed to open template file: "
                      << *template_path << std::endl;
            return 1;
        }
        std::stringstream buffer{};
        buffer << template_file.rdbuf();
        template_content = buffer.str();
        template_file.close();

        generator.generate_config_header(output_path, template_content,
                                         inlines);

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

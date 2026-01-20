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

        bool is_define = true;
        nlohmann::json::const_iterator define_it = val.find("define");
        if (define_it == val.end() || define_it->is_null()) {
            // Check for "define_flag" or "is_define" for backward compatibility
            define_it = val.find("define_flag");
            if (define_it == val.end() || define_it->is_null()) {
                define_it = val.find("is_define");
            }
        }
        if (define_it != val.end() && !define_it->is_null()) {
            is_define = define_it->get<bool>();
        }

        bool is_subst = false;
        // Check for "subst", "subst_flag", or "is_subst" for compatibility
        nlohmann::json::const_iterator subst_it = val.find("subst");
        if (subst_it == val.end() || subst_it->is_null()) {
            subst_it = val.find("subst_flag");
            if (subst_it == val.end() || subst_it->is_null()) {
                subst_it = val.find("is_subst");
            }
        }
        if (subst_it != val.end() && !subst_it->is_null()) {
            is_subst = subst_it->get<bool>();
        }

        // Check for type (defaults to kDefine for backward compatibility)
        CheckType type = CheckType::kDefine;
        nlohmann::json::const_iterator type_it = val.find("type");
        if (type_it != val.end() && type_it->is_string()) {
            std::string type_str = type_it->get<std::string>();
            // Parse type string to CheckType enum (same logic as check_result.cc)
            if (type_str == "header") {
                type = CheckType::kHeader;
            } else if (type_str == "function") {
                type = CheckType::kFunction;
            } else if (type_str == "lib") {
                type = CheckType::kLib;
            } else if (type_str == "symbol") {
                type = CheckType::kSymbol;
            } else if (type_str == "type") {
                type = CheckType::kType;
            } else if (type_str == "compile") {
                type = CheckType::kCompile;
            } else if (type_str == "link") {
                type = CheckType::kLink;
            } else if (type_str == "define") {
                type = CheckType::kDefine;
            } else if (type_str == "define_unquoted") {
                type = CheckType::kDefineUnquoted;
            } else if (type_str == "subst") {
                type = CheckType::kSubst;
            } else if (type_str == "m4_define") {
                type = CheckType::kM4Define;
            } else if (type_str == "sizeof") {
                type = CheckType::kSizeof;
            } else if (type_str == "alignof") {
                type = CheckType::kAlignof;
            } else if (type_str == "compute_int") {
                type = CheckType::kComputeInt;
            } else if (type_str == "endian") {
                type = CheckType::kEndian;
            } else if (type_str == "decl") {
                type = CheckType::kDecl;
            } else if (type_str == "member") {
                type = CheckType::kMember;
            }
        }

        loaded_results.emplace_back(define, value, success, is_define, is_subst, type);
    }

    return loaded_results;
}

}  // namespace

int Resolver::resolve_and_generate(
    const std::vector<std::filesystem::path>& results_paths,
    const std::optional<std::filesystem::path>& template_path,
    const std::filesystem::path& output_path,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions,
    const std::string& mode) {
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

        // Convert mode string to enum
        Mode mode_enum = Mode::kDefines;
        if (mode == "subst") {
            mode_enum = Mode::kSubst;
        } else if (mode == "all") {
            mode_enum = Mode::kAll;
        }

        // Generate header
        SourceGenerator generator(results, mode_enum);

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

        generator.generate_config_header(output_path, template_content, inlines,
                                         substitutions);

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

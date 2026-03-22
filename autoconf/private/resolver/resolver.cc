#include "autoconf/private/resolver/resolver.h"

#include <algorithm>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/common/file_util.h"
#include "autoconf/private/resolver/source_generator.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

namespace {

/**
 * @brief Load a flat result file and construct a CheckResult.
 *
 * Flat result files contain only {success, value, type}. Consumer metadata
 * (name, define, subst, unquote, is_define, is_subst) is provided externally
 * from the manifest.
 *
 * @param name The define/subst name from the manifest key.
 * @param path Path to the flat result JSON file.
 * @param is_define Whether this result is a define.
 * @param is_subst Whether this result is a subst variable.
 * @param unquote Whether this is AC_DEFINE_UNQUOTED.
 * @return CheckResult with all fields populated.
 */
CheckResult load_flat_result(const std::string& name,
                             const std::filesystem::path& path, bool is_define,
                             bool is_subst, bool unquote) {
    if (!file_exists(path)) {
        throw std::runtime_error("Result file does not exist: " +
                                 path.string());
    }

    std::ifstream file = open_ifstream(path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open result file: " +
                                 path.string());
    }

    nlohmann::json j;
    file >> j;
    file.close();

    if (j.is_null() || !j.is_object()) {
        throw std::runtime_error("Invalid result JSON in: " + path.string());
    }

    std::optional<CheckResult> result = CheckResult::from_json(name, &j);
    if (!result.has_value()) {
        throw std::runtime_error("Failed to parse result from: " +
                                 path.string());
    }

    result->is_define = is_define;
    result->is_subst = is_subst;
    result->unquote = unquote;
    if (is_define) {
        result->define = name;
    }
    if (is_subst) {
        result->subst = name;
    }

    return *result;
}

/**
 * @brief Load results from a manifest section (defines or substs).
 *
 * Each entry maps a name to {"path": "...", "unquote": bool}.
 *
 * @param section The manifest section JSON object.
 * @param is_define Whether entries are defines.
 * @param is_subst Whether entries are subst variables.
 * @return Vector of CheckResult objects.
 */
std::vector<CheckResult> load_manifest_section(const nlohmann::json& section,
                                               bool is_define, bool is_subst) {
    std::vector<CheckResult> results;

    for (auto it = section.begin(); it != section.end(); ++it) {
        const std::string& name = it.key();
        const nlohmann::json& entry = it.value();

        if (!entry.is_object() || !entry.contains("path")) {
            throw std::runtime_error("Manifest entry '" + name +
                                     "' is missing required 'path' field");
        }

        std::string path = entry["path"].get<std::string>();
        bool unquote = entry.value("unquote", false);

        results.push_back(
            load_flat_result(name, path, is_define, is_subst, unquote));
    }

    return results;
}

}  // namespace

int Resolver::resolve_and_generate(
    const std::filesystem::path& manifest_path,
    const std::filesystem::path& template_path,
    const std::filesystem::path& output_path,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions, Mode mode) {
    try {
        if (!file_exists(manifest_path)) {
            throw std::runtime_error("Manifest file does not exist: " +
                                     manifest_path.string());
        }

        std::ifstream manifest_file = open_ifstream(manifest_path);
        if (!manifest_file.is_open()) {
            throw std::runtime_error("Failed to open manifest file: " +
                                     manifest_path.string());
        }

        nlohmann::json manifest;
        manifest_file >> manifest;
        manifest_file.close();

        if (!manifest.is_object()) {
            throw std::runtime_error("Manifest is not a JSON object: " +
                                     manifest_path.string());
        }

        nlohmann::json defines_section =
            manifest.value("defines", nlohmann::json::object());
        nlohmann::json substs_section =
            manifest.value("substs", nlohmann::json::object());

        std::vector<CheckResult> define_results =
            load_manifest_section(defines_section, true, false);
        std::vector<CheckResult> subst_results =
            load_manifest_section(substs_section, false, true);

        std::vector<CheckResult> cache_results;

        if (DebugLogger::is_debug_enabled()) {
            std::vector<CheckResult> sorted_defines = define_results;
            std::sort(sorted_defines.begin(), sorted_defines.end(),
                      [](const CheckResult& a, const CheckResult& b) {
                          return a.name < b.name;
                      });
            for (const CheckResult& result : sorted_defines) {
                std::string status = result.success ? "yes" : "no";
                DebugLogger::log("checking " + result.name + "... " + status);
            }
        }

        SourceGenerator generator(cache_results, define_results, subst_results,
                                  mode);

        std::ifstream template_file = open_ifstream(template_path);
        if (!template_file.is_open()) {
            std::cerr << "Error: Failed to open template file: "
                      << template_path << std::endl;
            return 1;
        }
        std::stringstream buffer{};
        buffer << template_file.rdbuf();
        std::string template_content = buffer.str();
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

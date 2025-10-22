#include "autoconf/private/checker/config.h"

#include <algorithm>
#include <fstream>
#include <set>
#include <sstream>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

std::unique_ptr<Config> Config::from_file(
    const std::filesystem::path& config_path) {
    std::unique_ptr<Config> config = std::make_unique<Config>();

    std::ifstream file(config_path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open config file: " +
                                 config_path.string());
    }

    nlohmann::json doc;
    file >> doc;

    // List of required fields
    const std::vector<std::string> required_fields = {
        "c_compiler",    "c_flags",      "c_link_flags", "checks",
        "compiler_type", "cpp_compiler", "cpp_flags",    "cpp_link_flags",
        "linker",        "package_info"};

    // Validate all required fields are present
    for (const std::string& field : required_fields) {
        if (!doc.contains(field)) {
            throw std::runtime_error("Missing required field: '" + field + "'");
        }
    }

    // Parse required fields
    if (!doc["c_compiler"].is_string()) {
        throw std::runtime_error(
            "Invalid 'c_compiler' field: must be a string");
    }
    config->c_compiler = doc["c_compiler"].get<std::string>();

    if (!doc["cpp_compiler"].is_string()) {
        throw std::runtime_error(
            "Invalid 'cpp_compiler' field: must be a string");
    }
    config->cpp_compiler = doc["cpp_compiler"].get<std::string>();

    // Parse compiler flags (required, must be array)
    if (!doc["c_flags"].is_array()) {
        throw std::runtime_error("Invalid 'c_flags' field: must be an array");
    }
    for (const nlohmann::json& flag : doc["c_flags"]) {
        if (flag.is_string()) {
            config->c_flags.push_back(flag.get<std::string>());
        }
    }

    if (!doc["cpp_flags"].is_array()) {
        throw std::runtime_error("Invalid 'cpp_flags' field: must be an array");
    }
    for (const nlohmann::json& flag : doc["cpp_flags"]) {
        if (flag.is_string()) {
            config->cpp_flags.push_back(flag.get<std::string>());
        }
    }

    // Parse linker flags (required, must be array)
    if (!doc["c_link_flags"].is_array()) {
        throw std::runtime_error(
            "Invalid 'c_link_flags' field: must be an array");
    }
    for (const nlohmann::json& flag : doc["c_link_flags"]) {
        if (flag.is_string()) {
            config->c_link_flags.push_back(flag.get<std::string>());
        }
    }

    if (!doc["cpp_link_flags"].is_array()) {
        throw std::runtime_error(
            "Invalid 'cpp_link_flags' field: must be an array");
    }
    for (const nlohmann::json& flag : doc["cpp_link_flags"]) {
        if (flag.is_string()) {
            config->cpp_link_flags.push_back(flag.get<std::string>());
        }
    }

    // Parse linker (required, must be string)
    if (!doc["linker"].is_string()) {
        throw std::runtime_error("Invalid 'linker' field: must be a string");
    }
    config->linker = doc["linker"].get<std::string>();

    // Parse compiler type (required, must be string)
    if (!doc["compiler_type"].is_string()) {
        throw std::runtime_error(
            "Invalid 'compiler_type' field: must be a string");
    }
    config->compiler_type = doc["compiler_type"].get<std::string>();

    // Parse checks (required, must be array)
    if (!doc["checks"].is_array()) {
        throw std::runtime_error("Invalid 'checks' field: must be an array");
    }

    for (const nlohmann::json& check_json : doc["checks"]) {
        std::optional<Check> check_opt = Check::from_json(&check_json);
        if (check_opt.has_value()) {
            config->checks.push_back(*check_opt);
        }
    }

    // Parse package_info file path (required, must be string)
    if (!doc["package_info"].is_string()) {
        throw std::runtime_error(
            "Invalid 'package_info' field: must be a string");
    }
    std::string package_info_path = doc["package_info"].get<std::string>();

    // Read package_info file and extract name/version
    std::ifstream package_info_file(package_info_path);
    if (!package_info_file.is_open()) {
        throw std::runtime_error("Failed to open package_info file: " +
                                 package_info_path);
    }

    nlohmann::json package_info_json;
    package_info_file >> package_info_json;

    // Extract name and version (both are optional in the package_info file)
    if (package_info_json.contains("name") &&
        package_info_json["name"].is_string()) {
        config->package_name = package_info_json["name"].get<std::string>();
    }

    if (package_info_json.contains("version") &&
        package_info_json["version"].is_string()) {
        config->package_version =
            package_info_json["version"].get<std::string>();
    }

    // Parse optional defines field
    if (doc.contains("defines") && doc["defines"].is_object()) {
        for (nlohmann::json::const_iterator it = doc["defines"].begin();
             it != doc["defines"].end(); ++it) {
            if (it.value().is_string()) {
                config->defines[it.key()] = it.value().get<std::string>();
            }
        }
    }

    return config;
}

/**
 * @brief Extract standard header defines from a template string.
 *
 * This function mimics GNU autoconf's autoheader behavior by detecting
 * standard header defines (like HAVE_STDIO_H) in templates and converting
 * them to header names. It looks for patterns like "#undef HAVE_STDIO_H" or
 * "/ * #undef HAVE_STDIO_H * /" and converts them to header names (e.g.,
 * "HAVE_STDIO_H" -> "stdio.h", "HAVE_SYS_STAT_H" -> "sys/stat.h").
 *
 * @param template_content Template content as a string.
 * @return Map from define names to header names (e.g., {"HAVE_STDIO_H":
 * "stdio.h"}).
 */
std::map<std::string, std::string> extract_header_defines_from_template(
    const std::string& template_content) {
    std::map<std::string, std::string> header_defines;

    std::istringstream stream(template_content);
    std::string line;
    while (std::getline(stream, line)) {
        // Look for #undef HAVE_*_H patterns
        if (line.find("#undef") != std::string::npos &&
            line.find("HAVE_") != std::string::npos &&
            line.find("_H") != std::string::npos) {
            // Extract the define name
            // Handle both "#undef HAVE_STDIO_H" and "/* #undef HAVE_STDIO_H */"
            size_t undef_pos = line.find("#undef");
            if (undef_pos != std::string::npos) {
                std::string after_undef =
                    line.substr(undef_pos + 6);  // Skip "#undef"
                // Remove comment markers and whitespace
                size_t comment_start = after_undef.find("/*");
                if (comment_start != std::string::npos) {
                    after_undef = after_undef.substr(0, comment_start);
                }
                size_t comment_end = after_undef.find("*/");
                if (comment_end != std::string::npos) {
                    after_undef = after_undef.substr(comment_end + 2);
                }

                // Trim whitespace
                size_t start = after_undef.find_first_not_of(" \t");
                if (start != std::string::npos) {
                    after_undef = after_undef.substr(start);
                }
                size_t end = after_undef.find_last_not_of(" \t");
                if (end != std::string::npos) {
                    after_undef = after_undef.substr(0, end + 1);
                }

                // Check if it's a header define (HAVE_*_H)
                if (after_undef.size() > 6 &&
                    after_undef.substr(0, 5) == "HAVE_" &&
                    after_undef.substr(after_undef.size() - 2) == "_H") {
                    // Convert HAVE_STDIO_H -> stdio.h
                    // Convert HAVE_SYS_STAT_H -> sys/stat.h
                    std::string header_part = after_undef.substr(
                        5);  // Remove "HAVE_" -> "STDIO_H" or "SYS_STAT_H"
                    header_part = header_part.substr(
                        0, header_part.size() -
                               2);  // Remove "_H" -> "STDIO" or "SYS_STAT"
                    // Convert to lowercase and replace underscores with slashes
                    std::string header_name = header_part;
                    std::transform(header_name.begin(), header_name.end(),
                                   header_name.begin(), ::tolower);
                    // Replace underscores with slashes for sys headers
                    std::replace(header_name.begin(), header_name.end(), '_',
                                 '/');
                    // Add .h extension
                    header_name += ".h";
                    header_defines[after_undef] = header_name;
                }
            }
        }
    }

    return header_defines;
}

/**
 * @brief Check if template contains STDC_HEADERS define.
 *
 * STDC_HEADERS is a special define that indicates all standard C89 headers
 * are available. This function checks if the template file references this
 * define.
 *
 * @param template_content Template content as a string.
 * @return True if STDC_HEADERS is found in the template, false otherwise.
 */
bool template_has_stdc_headers(const std::string& template_content) {
    return template_content.find("STDC_HEADERS") != std::string::npos;
}

Config Config::with_template_checks(const std::string& template_content) const {
    // Create a copy of this config
    Config new_config = *this;

    // Extract header defines from template
    std::map<std::string, std::string> template_headers =
        extract_header_defines_from_template(template_content);

    // Build set of existing defines
    std::set<std::string> existing_defines;
    for (const Check& check : new_config.checks) {
        existing_defines.insert(check.define());
    }

    // Add checks for headers found in template but not in existing checks
    for (const auto& [define_name, header_name] : template_headers) {
        if (existing_defines.find(define_name) == existing_defines.end()) {
            // Create a header check using JSON (since Check constructor is
            // private)
            nlohmann::json header_json;
            header_json["type"] = "header";
            header_json["name"] = header_name;
            header_json["define"] = define_name;
            header_json["language"] = "c";
            std::optional<Check> header_check_opt =
                Check::from_json(&header_json);
            if (header_check_opt.has_value()) {
                new_config.checks.push_back(*header_check_opt);
                existing_defines.insert(define_name);
            }
        }
    }

    // Check for STDC_HEADERS and add it if needed
    if (template_has_stdc_headers(template_content) &&
        existing_defines.find("STDC_HEADERS") == existing_defines.end()) {
        // STDC_HEADERS checks if all standard C89 headers exist
        std::string stdc_headers_code = R"(#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <strings.h>
#include <sys/types.h>
#include <sys/stat.h>
#ifndef _WIN32
#include <unistd.h>
#endif

int main(void) { return 0; }
)";
        // Create check using JSON
        nlohmann::json stdc_json;
        stdc_json["type"] = "compile";
        stdc_json["name"] = "stdc_headers";
        stdc_json["define"] = "STDC_HEADERS";
        stdc_json["language"] = "c";
        stdc_json["code"] = stdc_headers_code;
        std::optional<Check> stdc_check_opt = Check::from_json(&stdc_json);
        if (stdc_check_opt.has_value()) {
            new_config.checks.push_back(*stdc_check_opt);
        }
    }

    return new_config;
}

}  // namespace rules_cc_autoconf

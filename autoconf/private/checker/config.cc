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

    nlohmann::json doc{};
    file >> doc;

    // List of required fields
    const std::vector<std::string> required_fields = {
        "c_compiler", "c_flags",        "c_link_flags",
        "checks",     "compiler_type",  "cpp_compiler",
        "cpp_flags",  "cpp_link_flags", "linker"};

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

    return config;
}

}  // namespace rules_cc_autoconf

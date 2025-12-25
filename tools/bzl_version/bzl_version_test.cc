/**
 * @file bzl_version_test.cc
 * @brief A test binary for ensuring the `MODULE.bazel` file and
 * VERSION variables are in sync.
 */

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "rules_cc/cc/runfiles/runfiles.h"

using rules_cc::cc::runfiles::Runfiles;

// Add support for Bazel 7
#ifndef BAZEL_CURRENT_REPOSITORY
#define BAZEL_CURRENT_REPOSITORY "_main"
#endif

/**
 * @brief Parse a `MODULE.bazel` file's content for the current module's
 * version.
 *
 * @param text The content of the `MODULE.bazel` file.
 * @param output An output parameter for the detected version.
 * @return True if successful.
 */
bool parse_module_bazel_version(const std::string& text, std::string& output) {
    bool found_module = false;
    std::istringstream stream(text);
    std::string line = {};

    while (std::getline(stream, line)) {
        if (found_module) {
            if (line.size() > 0 && line.back() == ')') {
                std::cerr << "Failed to parse version" << std::endl;
                return false;
            }
            std::size_t pos = line.rfind(" = ");
            if (pos != std::string::npos) {
                std::string param = line.substr(0, pos);
                std::string value = line.substr(pos + 3);

                // Trim param and value
                param.erase(0, param.find_first_not_of(" \t"));
                param.erase(param.find_last_not_of(" \t") + 1);
                value.erase(0, value.find_first_not_of(" \t"));
                value.erase(value.find_last_not_of(" \t,") + 1);
                if (!value.empty() && value.front() == '"' &&
                    value.back() == '"') {
                    value = value.substr(1, value.length() - 2);
                }

                if (param == "version") {
                    output = value;
                    return true;
                }
            }
        } else if (line.rfind("module(", 0) == 0) {
            found_module = true;
        }
    }

    std::cerr << "Failed to find MODULE.bazel version" << std::endl;
    return false;
}

int main() {
    const char* version_env = std::getenv("VERSION");
    const char* module_bazel_env = std::getenv("MODULE_BAZEL");

    if (!version_env || !module_bazel_env) {
        std::cerr
            << "VERSION and MODULE_BAZEL environment variables must be set."
            << std::endl;
        return 1;
    }

    std::string error = {};
    std::unique_ptr<Runfiles> runfiles(
        Runfiles::CreateForTest(BAZEL_CURRENT_REPOSITORY, &error));

    if (!error.empty()) {
        std::cerr << "Error creating runfiles: " << error << std::endl;
        return 1;
    }

    std::string module_bazel_path = runfiles->Rlocation(module_bazel_env);
    if (module_bazel_path.empty()) {
        std::cerr << "Failed to locate runfile: " << module_bazel_env
                  << std::endl;
        return 1;
    }

    std::string version(version_env);
    std::ifstream file(module_bazel_path);
    if (!file.is_open()) {
        std::cerr << "Failed to open " << module_bazel_env << std::endl;
        return 1;
    }

    std::stringstream buffer{};
    buffer << file.rdbuf();
    std::string module_bazel_text = buffer.str();

    std::string parsed_version = {};
    if (!parse_module_bazel_version(module_bazel_text, parsed_version)) {
        return 1;
    }

    if (version != parsed_version) {
        std::cerr << "Mismatch: version.bzl has \"" << version
                  << "\", but MODULE.bazel has \"" << parsed_version << "\""
                  << std::endl;
        return 1;
    }

    std::cout << "Versions match: " << version << std::endl;
    return 0;
}

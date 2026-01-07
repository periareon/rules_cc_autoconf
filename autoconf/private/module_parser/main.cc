/**
 * @brief Parses MODULE.bazel to extract package name and version information.
 */

#include <fstream>
#include <iostream>
#include <optional>
#include <regex>
#include <sstream>
#include <string>

#include "autoconf/private/json/json.h"

/**
 * @brief Extract a string value from a Starlark function call parameter.
 *
 * Parses patterns like `name = "value"` or `name="value"` and returns the
 * string value.
 *
 * @param content The file content to search in.
 * @param param_name The parameter name to extract (e.g., "name" or "version").
 * @return The extracted string value, or empty string if not found.
 */
std::string extract_string_param(const std::string& content,
                                 const std::string& param_name) {
    // Pattern to match: param_name = "value" or param_name="value"
    // Handles whitespace variations
    std::string pattern_str = param_name + "\\s*=\\s*\"([^\"]+)\"";
    std::regex pattern(pattern_str);
    std::smatch match;

    if (std::regex_search(content, match, pattern)) {
        if (match.size() >= 2) {
            return match[1].str();
        }
    }

    return "";
}

/**
 * @brief Find the module() call in the content and extract name and version.
 *
 * @param content The MODULE.bazel file content.
 * @param name Output parameter for the module name.
 * @param version Output parameter for the module version.
 * @return true if both name and version were found, false otherwise.
 */
bool parse_module(const std::string& content, std::string& name,
                  std::string& version) {
    // Find the module( ... ) block
    std::regex module_pattern(R"(module\s*\()");
    std::smatch module_match;

    if (!std::regex_search(content, module_match, module_pattern)) {
        return false;
    }

    // Find the closing parenthesis for the module() call
    // We'll search from the opening parenthesis
    size_t start_pos = module_match.position() + module_match.length();
    int paren_count = 1;
    size_t end_pos = start_pos;

    for (size_t i = start_pos; i < content.length() && paren_count > 0; ++i) {
        if (content[i] == '(') {
            paren_count++;
        } else if (content[i] == ')') {
            paren_count--;
            if (paren_count == 0) {
                end_pos = i;
                break;
            }
        }
    }

    if (paren_count != 0) {
        // Didn't find matching closing parenthesis
        return false;
    }

    // Extract the content between module( and )
    std::string module_content = content.substr(start_pos, end_pos - start_pos);

    // Extract name and version
    name = extract_string_param(module_content, "name");
    version = extract_string_param(module_content, "version");

    return !name.empty() && !version.empty();
}

/**
 * @brief Parsed command-line arguments for module_parser binary.
 */
struct ModuleParserArgs {
    /** Path to MODULE.bazel file to parse */
    std::string module_bazel{};

    /** Path to output JSON file for PACKAGE_NAME */
    std::string out_name{};

    /** Path to output JSON file for PACKAGE_VERSION */
    std::string out_version{};

    /** Path to output JSON file for PACKAGE_STRING (optional) */
    std::string out_string{};

    /** An optional override name to use instead of what's parsed from
     * `module_bazel` (optional) */
    std::string forced_name{};

    /** An optional override version to use instead of what's parsed from
     * `module_bazel` (optional) */
    std::string forced_version{};

    /** Whether to show help */
    bool show_help = false;
};

/**
 * @brief Print usage information.
 *
 * @param program_name Name of the program (argv[0]).
 */
void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name
              << " --module-bazel <file> --out-name <file> --out-version "
                 "<file> [--out-string <file>]"
              << std::endl;
    std::cout << "Options:" << std::endl;
    std::cout << "  --module-bazel <file>       Path to MODULE.bazel file to "
                 "parse (required)"
              << std::endl;
    std::cout << "  --out-name <file>           Path to output JSON file for "
                 "PACKAGE_NAME (required)"
              << std::endl;
    std::cout << "  --out-version <file>        Path to output JSON file for "
                 "PACKAGE_VERSION (required)"
              << std::endl;
    std::cout << "  --out-string <file>        Path to output JSON file for "
                 "PACKAGE_STRING (optional)"
              << std::endl;
    std::cout << "  --force-name <string>      A name to use instead over that "
                 "from `--module-bazel`"
              << std::endl;
    std::cout << "  --force-version <string>   A version to use instead over "
                 "that from `--module-bazel` PACKAGE_STRING (optional)"
              << std::endl;
    std::cout << "  --help                 Show this help message" << std::endl;
}

/**
 * @brief Parse command-line arguments.
 *
 * @param argc Number of arguments.
 * @param argv Array of argument strings.
 * @return Parsed ModuleParserArgs struct, or std::nullopt if parsing failed.
 */
std::optional<ModuleParserArgs> parse_args(int argc, char* argv[]) {
    ModuleParserArgs args;
    args.show_help = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--module-bazel") {
            if (i + 1 < argc) {
                args.module_bazel = argv[++i];
            } else {
                std::cerr << "Error: --module-bazel requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--out-name") {
            if (i + 1 < argc) {
                args.out_name = argv[++i];
            } else {
                std::cerr << "Error: --out-name requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--out-version") {
            if (i + 1 < argc) {
                args.out_version = argv[++i];
            } else {
                std::cerr << "Error: --out-version requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--out-string") {
            if (i + 1 < argc) {
                args.out_string = argv[++i];
            } else {
                std::cerr << "Error: --out-string requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--force-name") {
            if (i + 1 < argc) {
                args.forced_name = argv[++i];
            } else {
                std::cerr << "Error: --force-name requires a value"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--force-version") {
            if (i + 1 < argc) {
                args.forced_version = argv[++i];
            } else {
                std::cerr << "Error: --force-version requires a value"
                          << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    if (args.module_bazel.empty()) {
        std::cerr << "Error: --module-bazel is required" << std::endl;
        return std::nullopt;
    }
    if (args.out_name.empty()) {
        std::cerr << "Error: --out-name is required" << std::endl;
        return std::nullopt;
    }
    if (args.out_version.empty()) {
        std::cerr << "Error: --out-version is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

int main(int argc, char* argv[]) {
    std::optional<ModuleParserArgs> args_opt = parse_args(argc, argv);
    if (!args_opt.has_value()) {
        return 1;
    }

    ModuleParserArgs args = args_opt.value();
    if (args.show_help) {
        print_usage(argv[0]);
        return 0;
    }

    // Read the MODULE.bazel file
    std::ifstream file(args.module_bazel);
    if (!file.is_open()) {
        std::cerr << "Error: Could not open file: " << args.module_bazel
                  << std::endl;
        return 1;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();
    std::string content = buffer.str();
    file.close();

    // Parse the module definition
    std::string name{};
    std::string version{};
    if (!parse_module(content, name, version)) {
        std::cerr << "Error: Could not parse module definition from "
                  << args.module_bazel << std::endl;
        std::cerr
            << "Expected format: module(name = \"...\", version = \"...\")"
            << std::endl;
        return 1;
    }

    // Replace parsed values with any forced values.
    if (!args.forced_name.empty()) {
        name = args.forced_name;
    }
    if (!args.forced_version.empty()) {
        version = args.forced_version;
    }

    // Write PACKAGE_NAME JSON file in check result format
    std::ofstream out_name(args.out_name);
    if (!out_name.is_open()) {
        std::cerr << "Error: Could not open output file: " << args.out_name
                  << std::endl;
        return 1;
    }
    // Format matches check result JSON: { "DEFINE_NAME": { "value": "...",
    // "success": true } }
    // Ensure value is explicitly a JSON string (will be properly escaped and
    // quoted)
    nlohmann::json name_json = nlohmann::json::object();
    name_json["PACKAGE_NAME"] = {
        {"value", "\"" + name + "\""},
        {"success", true},
    };
    out_name << name_json.dump(4) << std::endl;
    out_name.close();

    // Write PACKAGE_VERSION JSON file in check result format
    std::ofstream out_version(args.out_version);
    if (!out_version.is_open()) {
        std::cerr << "Error: Could not open output file: " << args.out_version
                  << std::endl;
        return 1;
    }
    // Format matches check result JSON: { "DEFINE_NAME": { "value": "...",
    // "success": true } }
    nlohmann::json version_json = nlohmann::json::object();
    version_json["PACKAGE_VERSION"] = {
        {"value", "\"" + version + "\""},
        {"success", true},
    };
    out_version << version_json.dump(4) << std::endl;
    out_version.close();

    // Write PACKAGE_STRING JSON file if requested
    if (!args.out_string.empty()) {
        std::string package_string = name + " " + version;
        std::ofstream out_string(args.out_string);
        if (!out_string.is_open()) {
            std::cerr << "Error: Could not open output file: "
                      << args.out_string << std::endl;
            return 1;
        }
        // Format matches check result JSON: { "DEFINE_NAME": { "value": "...",
        // "success": true } }
        nlohmann::json string_json = nlohmann::json::object();
        string_json["PACKAGE_STRING"] = {
            {"value", "\"" + package_string + "\""},
            {"success", true},
        };
        out_string << string_json.dump(4) << std::endl;
        out_string.close();
    }

    return 0;
}

/**
 * @brief Parses MODULE.bazel to extract package name and version information.
 */

#include <fstream>
#include <iostream>
#include <optional>
#include <regex>
#include <sstream>
#include <string>

#include "tools/json/json.h"

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

    /** Path to output JSON file for PACKAGE_TARNAME (optional) */
    std::string out_tarname{};

    /** An optional override name to use instead of what's parsed from
     * `module_bazel` (optional) */
    std::string forced_name{};

    /** An optional override version to use instead of what's parsed from
     * `module_bazel` (optional) */
    std::string forced_version{};

    /** An optional override tarname to use instead of defaulting to name
     * (optional) */
    std::string forced_tarname{};

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
    std::cout << "  --out-tarname <file>       Path to output JSON file for "
                 "PACKAGE_TARNAME (optional)"
              << std::endl;
    std::cout << "  --force-name <string>      A name to use instead over that "
                 "from `--module-bazel`"
              << std::endl;
    std::cout << "  --force-version <string>   A version to use instead over "
                 "that from `--module-bazel`"
              << std::endl;
    std::cout << "  --force-tarname <string>   A tarname to use instead of "
                 "defaulting to name"
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
        } else if (arg == "--out-tarname") {
            if (i + 1 < argc) {
                args.out_tarname = argv[++i];
            } else {
                std::cerr << "Error: --out-tarname requires a file path"
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
        } else if (arg == "--force-tarname") {
            if (i + 1 < argc) {
                args.forced_tarname = argv[++i];
            } else {
                std::cerr << "Error: --force-tarname requires a value"
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

/**
 * @brief Write a check result JSON file for a package define.
 *
 * Writes JSON in the format: { "DEFINE_NAME": { "value": "\"...\""", "success":
 * true } }
 *
 * @param path Path to the output file.
 * @param define_name The define name (e.g., "PACKAGE_NAME").
 * @param value The string value (will be quoted in JSON).
 * @return true on success, false on failure (error printed to stderr).
 */
bool write_package_json(const std::string& path, const std::string& define_name,
                        const std::string& value) {
    std::ofstream out(path);
    if (!out.is_open()) {
        std::cerr << "Error: Could not open output file: " << path << std::endl;
        return false;
    }
    nlohmann::json j = nlohmann::json::object();
    j[define_name] = {
        {"value", "\"" + value + "\""},
        {"success", true},
    };
    out << j.dump(4) << std::endl;
    out.close();
    return true;
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

    // Compute tarname: use forced_tarname if provided, otherwise default to
    // name
    std::string tarname =
        args.forced_tarname.empty() ? name : args.forced_tarname;

    if (!write_package_json(args.out_name, "PACKAGE_NAME", name)) return 1;
    if (!write_package_json(args.out_version, "PACKAGE_VERSION", version))
        return 1;

    if (!args.out_string.empty()) {
        if (!write_package_json(args.out_string, "PACKAGE_STRING",
                                name + " " + version))
            return 1;
    }

    if (!args.out_tarname.empty()) {
        if (!write_package_json(args.out_tarname, "PACKAGE_TARNAME", tarname))
            return 1;
    }

    return 0;
}

#include <cstdio>
#include <fstream>
#include <iostream>
#include <regex>
#include <sstream>
#include <string>

/**
 * @brief Escape a string for JSON output.
 *
 * Escapes special characters like quotes, backslashes, and control characters.
 *
 * @param str The string to escape.
 * @return The escaped string.
 */
std::string escape_json_string(const std::string& str) {
    std::string escaped;
    for (char c : str) {
        switch (c) {
            case '"':
                escaped += "\\\"";
                break;
            case '\\':
                escaped += "\\\\";
                break;
            case '\b':
                escaped += "\\b";
                break;
            case '\f':
                escaped += "\\f";
                break;
            case '\n':
                escaped += "\\n";
                break;
            case '\r':
                escaped += "\\r";
                break;
            case '\t':
                escaped += "\\t";
                break;
            default:
                // Escape control characters
                if (c < 0x20) {
                    char buf[7];
                    snprintf(buf, sizeof(buf), "\\u%04x",
                             static_cast<unsigned char>(c));
                    escaped += buf;
                } else {
                    escaped += c;
                }
                break;
        }
    }
    return escaped;
}

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

int main(int argc, char* argv[]) {
    if (argc != 3) {
        std::cerr << "Usage: " << argv[0] << " <MODULE.bazel> <output.json>"
                  << std::endl;
        return 1;
    }

    const std::string module_file = argv[1];
    const std::string output_file = argv[2];

    // Read the MODULE.bazel file
    std::ifstream file(module_file);
    if (!file.is_open()) {
        std::cerr << "Error: Could not open file: " << module_file << std::endl;
        return 1;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();
    std::string content = buffer.str();
    file.close();

    // Parse the module definition
    std::string name, version;
    if (!parse_module(content, name, version)) {
        std::cerr << "Error: Could not parse module definition from "
                  << module_file << std::endl;
        std::cerr
            << "Expected format: module(name = \"...\", version = \"...\")"
            << std::endl;
        return 1;
    }

    // Write JSON to output file
    std::ofstream out(output_file);
    if (!out.is_open()) {
        std::cerr << "Error: Could not open output file: " << output_file
                  << std::endl;
        return 1;
    }

    // Write JSON with indentation (4 spaces) to match the format used in
    // autoconf_rule.bzl
    out << "{\n"
        << "    \"name\": \"" << escape_json_string(name) << "\",\n"
        << "    \"version\": \"" << escape_json_string(version) << "\"\n"
        << "}\n";
    out.close();

    return 0;
}

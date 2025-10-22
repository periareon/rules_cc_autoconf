#include "autoconf/private/resolver/source_generator.h"

#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <tuple>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Replace #undef statements with #define statements while preserving
 * trailing newlines.
 *
 * This function is used to process template files (config.h.in) by replacing
 * #undef statements with their corresponding #define statements based on check
 * results. It preserves the number of trailing newlines after each #undef
 * statement to maintain template formatting.
 *
 * @param content The template content to process.
 * @param define_name The name of the define to replace (e.g., "HAVE_STDIO_H").
 * @param replacement The replacement text (e.g., "#define HAVE_STDIO_H 1").
 * @param is_comment If true, replace with a commented-out #undef instead of
 * the replacement text.
 * @return The processed content with #undef statements replaced.
 */
std::string replace_undef(const std::string& content,
                          const std::string& define_name,
                          const std::string& replacement,
                          bool is_comment = false) {
    // Pattern: match "#undef DEFINE_NAME" followed by any newlines
    std::string pattern_str = R"(#undef\s+)" + define_name + R"((\n+))";
    std::regex pattern(pattern_str);

    std::string output{};
    std::string::const_iterator search_start(content.cbegin());
    std::smatch match{};

    while (std::regex_search(search_start, content.cend(), match, pattern)) {
        // Append everything before the match
        output.append(search_start, match[0].first);

        // Get the captured newlines (group 1)
        std::string newlines = match[1].str();

        // Append the replacement
        if (is_comment) {
            output += "/* #undef " + define_name + " */";
        } else {
            output += replacement;
        }

        // Preserve the captured newlines
        output += newlines;

        // Move past this match
        search_start = match[0].second;
    }

    // Append the rest of the content
    output.append(search_start, content.cend());

    return output;
}

SourceGenerator::SourceGenerator(const std::vector<CheckResult>& results)
    : results_(results) {}

void SourceGenerator::generate_config_header(
    const std::filesystem::path& output_path,
    const std::string& template_content,
    const std::map<std::string, std::filesystem::path>& inlines) {
    std::string content = process_template(template_content, inlines);

    // Preserve trailing newline behavior from template
    // If template had no trailing newline, remove any trailing newlines we
    // added
    bool template_has_trailing_newline =
        !template_content.empty() && template_content.back() == '\n';
    if (!template_has_trailing_newline) {
        // Remove trailing newlines to match template
        while (!content.empty() && content.back() == '\n') {
            content.pop_back();
        }
    }

    std::ofstream file(output_path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open output file: " +
                                 output_path.string());
    }

    file << content;
    file.close();
}

std::string SourceGenerator::process_template(
    const std::string& template_content,
    const std::map<std::string, std::filesystem::path>& inlines) {
    std::string content = template_content;

    // Initialize set of builtins that need to be processed
    std::set<std::string> builtins = {"PACKAGE_NAME",   "PACKAGE_VERSION",
                                      "PACKAGE_STRING", "PACKAGE_BUGREPORT",
                                      "PACKAGE_URL",    "PACKAGE_TARNAME"};

    // Track values for builtins as we process check results
    std::map<std::string, std::string> builtin_values;

    // Step 1: Iterate over check results and update defines, draining builtins
    for (const CheckResult& result : results_) {
        builtin_values[result.define] = result.value;

        // Replace @DEFINE@ patterns
        std::regex definePattern("@" + result.define + "@");
        content = std::regex_replace(content, definePattern, result.value);

        // Replace #undef DEFINE patterns (preserve all trailing newlines)
        if (result.success) {
            std::string replacement_text = "#define " + result.define;
            if (!result.value.empty()) {
                replacement_text += " " + result.value;
            }
            content =
                replace_undef(content, result.define, replacement_text, false);
        } else {
            content = replace_undef(content, result.define, "", true);
        }

        // Drain this builtin from the set if it's a builtin
        builtins.erase(result.define);
    }

    // Step 2: For any remaining builtins, do the substitution explicitly
    for (const std::string& builtin : builtins) {
        std::string value{};

        // Handle special cases for missing defines
        if (builtin == "PACKAGE_STRING") {
            std::map<std::string, std::string>::iterator name_it =
                builtin_values.find("PACKAGE_NAME");
            std::map<std::string, std::string>::iterator version_it =
                builtin_values.find("PACKAGE_VERSION");
            std::map<std::string, std::string>::iterator string_it =
                builtin_values.find("PACKAGE_STRING");
            if (string_it == builtin_values.end() ||
                string_it->second.empty()) {
                if (name_it != builtin_values.end() &&
                    version_it != builtin_values.end() &&
                    !name_it->second.empty() && !version_it->second.empty()) {
                    value = name_it->second + " " + version_it->second;
                }
            } else {
                value = string_it->second;
            }
        } else if (builtin == "PACKAGE_TARNAME") {
            std::map<std::string, std::string>::iterator name_it =
                builtin_values.find("PACKAGE_NAME");
            if (name_it != builtin_values.end()) {
                value = name_it->second;
            }
        } else {
            // Use value from builtin_values if available
            std::map<std::string, std::string>::iterator value_it =
                builtin_values.find(builtin);
            if (value_it != builtin_values.end()) {
                value = value_it->second;
            }
        }

        // Replace @PLACEHOLDER@ patterns
        std::regex placeholder_regex("@" + builtin + "@");
        content = std::regex_replace(content, placeholder_regex, value);

        // Replace #undef patterns
        std::string define_text = "#define " + builtin + " " + value;
        content = replace_undef(content, builtin, define_text);
    }

    // Step 3: Comment out any remaining #undef statements that don't have
    // a corresponding define (i.e., weren't processed in steps 1 or 2)
    // Pattern: match "#undef" followed by whitespace and an identifier
    std::regex undef_pattern(R"(#undef\s+([a-zA-Z_][a-zA-Z0-9_]*)(\n+))");
    std::string output{};
    std::string::const_iterator search_start(content.cbegin());
    std::smatch match{};

    while (
        std::regex_search(search_start, content.cend(), match, undef_pattern)) {
        // Append everything before the match
        output.append(search_start, match[0].first);

        // Get the captured define name (group 1) and newlines (group 2)
        std::string define_name = match[1].str();
        std::string newlines = match[2].str();

        // Comment out the #undef
        output += "/* #undef " + define_name + " */";
        output += newlines;

        // Move past this match
        search_start = match[0].second;
    }

    // Append the rest of the content
    output.append(search_start, content.cend());
    content = output;

    // Step 4: Process inline replacements
    for (const std::pair<const std::string, std::filesystem::path>&
             inline_pair : inlines) {
        const std::string& search_string = inline_pair.first;
        const std::filesystem::path& file_path = inline_pair.second;
        // Read the file content
        std::ifstream inline_file(file_path);
        if (!inline_file.is_open()) {
            throw std::runtime_error("Failed to open inline file: " +
                                     file_path.string());
        }
        std::stringstream buffer{};
        buffer << inline_file.rdbuf();
        std::string replacement_content = buffer.str();
        inline_file.close();

        // Find all positions of the search string in the CURRENT content
        // We collect positions first to ensure we only replace strings
        // that exist before any replacements are made
        std::vector<size_t> positions{};
        size_t pos = 0;
        while ((pos = content.find(search_string, pos)) != std::string::npos) {
            positions.push_back(pos);
            pos += search_string.length();
        }

        // Replace from the end backwards to avoid position shifts affecting
        // earlier replacements
        for (std::vector<size_t>::reverse_iterator it = positions.rbegin();
             it != positions.rend(); ++it) {
            content.replace(*it, search_string.length(), replacement_content);
        }
    }

    return content;
}

}  // namespace rules_cc_autoconf

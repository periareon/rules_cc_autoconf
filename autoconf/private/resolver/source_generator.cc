#include "autoconf/private/resolver/source_generator.h"

#include <cctype>
#include <fstream>
#include <sstream>
#include <unordered_map>
#include <vector>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

namespace {

/**
 * @brief Describes how to replace a single #undef line.
 */
struct UndefReplacement {
    std::string replacement;  ///< The replacement text (e.g., "#define FOO 1")
    bool is_comment;          ///< If true, comment out instead of replacing
};

/**
 * @brief Parse a single #undef line, extracting the spacing, name, and trailing
 * newlines.
 *
 * Given a position pointing at '#' in the content, attempts to parse
 * "#<spacing>undef<ws><NAME><newlines>".
 *
 * @param content The full content string.
 * @param pos Position of the '#' character.
 * @param[out] spacing Spacing between '#' and 'undef'.
 * @param[out] name The define name after 'undef'.
 * @param[out] newlines Trailing newline characters.
 * @param[out] match_end Position after the full match.
 * @return true if a valid #undef was parsed.
 */
bool parse_undef_at(const std::string& content, size_t pos,
                    std::string& spacing, std::string& name,
                    std::string& newlines, size_t& match_end) {
    size_t len = content.size();
    size_t i = pos + 1;  // skip '#'

    // Capture spacing between '#' and 'undef'
    spacing.clear();
    while (i < len && (content[i] == ' ' || content[i] == '\t')) {
        spacing += content[i];
        ++i;
    }

    // Expect "undef"
    if (i + 5 > len || content.compare(i, 5, "undef") != 0) {
        return false;
    }
    i += 5;

    // Expect at least one whitespace after "undef"
    if (i >= len || (content[i] != ' ' && content[i] != '\t')) {
        return false;
    }
    while (i < len && (content[i] == ' ' || content[i] == '\t')) {
        ++i;
    }

    // Capture identifier name
    name.clear();
    if (i >= len || (!std::isalpha(static_cast<unsigned char>(content[i])) &&
                     content[i] != '_')) {
        return false;
    }
    while (i < len && (std::isalnum(static_cast<unsigned char>(content[i])) ||
                       content[i] == '_')) {
        name += content[i];
        ++i;
    }

    // Capture trailing newlines (at least one required)
    newlines.clear();
    while (i < len && content[i] == '\n') {
        newlines += '\n';
        ++i;
    }
    if (newlines.empty()) {
        return false;
    }

    match_end = i;
    return true;
}

/**
 * @brief Single-pass replacement of all #undef statements in content.
 *
 * Scans content once, looking for "#undef NAME" patterns. For each match,
 * looks up the define name in the replacements map. If found, applies the
 * replacement (either a #define or a comment). If not found, either comments
 * out the undef or leaves it unchanged, depending on comment_remaining.
 *
 * This replaces the previous approach of compiling a separate regex and
 * scanning the entire content for each individual define name.
 *
 * @param content The template content to process.
 * @param replacements Map from define name to replacement info.
 * @param comment_remaining If true, undefs not in the map are commented out.
 * @return Processed content.
 */
std::string batch_replace_undefs(
    const std::string& content,
    const std::unordered_map<std::string, UndefReplacement>& replacements,
    bool comment_remaining) {
    std::string output;
    output.reserve(content.size());

    size_t len = content.size();
    size_t i = 0;

    while (i < len) {
        // Look for '#' that could start a #undef
        if (content[i] == '#') {
            std::string spacing, name, newlines;
            size_t match_end = 0;

            if (parse_undef_at(content, i, spacing, name, newlines,
                               match_end)) {
                std::unordered_map<std::string,
                                   UndefReplacement>::const_iterator it =
                    replacements.find(name);
                if (it != replacements.end()) {
                    if (it->second.is_comment) {
                        output += "/* #" + spacing + "undef " + name + " */";
                    } else {
                        const std::string& repl = it->second.replacement;
                        if (!repl.empty() && repl[0] == '#') {
                            output += "#" + spacing + repl.substr(1);
                        } else {
                            output += repl;
                        }
                    }
                } else if (comment_remaining) {
                    output += "/* #" + spacing + "undef " + name + " */";
                } else {
                    // Leave unchanged
                    output.append(content, i, match_end - i);
                }
                output += newlines;
                i = match_end;
                continue;
            }
        }
        output += content[i];
        ++i;
    }

    return output;
}

}  // namespace

SourceGenerator::SourceGenerator(const std::vector<CheckResult>& cache_results,
                                 const std::vector<CheckResult>& define_results,
                                 const std::vector<CheckResult>& subst_results,
                                 Mode mode)
    : cache_results_(cache_results),
      define_results_(define_results),
      subst_results_(subst_results),
      mode_(mode) {}

void SourceGenerator::generate_config_header(
    const std::filesystem::path& output_path,
    const std::string& template_content,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions) {
    std::string content =
        process_template(template_content, inlines, substitutions);

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
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions) {
    std::string content = template_content;

    // Step 1: Load and parse all data
    ProcessedData data = load_and_parse_data();

    // Step 4: Perform inlines and direct subst calls FIRST (before defines
    // replacement) This ensures substitutions can find #undef lines before they
    // get commented out
    content = process_inlines_and_direct_subst(content, inlines, substitutions);

    // Step 2: If in defines or all mode, do defines replacement (and comment
    // out undefs)
    content = process_defines_replacement(content, data);

    // Step 3: If in subst or all mode, do replacements
    content = process_subst_replacements(content, data);

    // If in subst mode (not all), comment out all #undef statements for defines
    // (In defines mode, this is already handled by process_defines_replacement)
    if (mode_ == Mode::kSubst) {
        content = comment_out_define_undefs(content, data);
    }

    // Step 5: Clean up end of file
    return cleanup_end_of_file(content);
}

// Step 1: Load and parse all data
SourceGenerator::ProcessedData SourceGenerator::load_and_parse_data() const {
    ProcessedData data;

    // Initialize builtins
    data.builtins = {"PACKAGE_NAME",      "PACKAGE_VERSION", "PACKAGE_STRING",
                     "PACKAGE_BUGREPORT", "PACKAGE_URL",     "PACKAGE_TARNAME"};

    // First, process cache_results to build a lookup map for conditions
    // Cache variables are available for condition evaluation
    std::map<std::string, const CheckResult*> cache_results_by_name;
    for (const CheckResult& result : cache_results_) {
        cache_results_by_name[result.name] = &result;
        // Cache variables are also available in results_by_name for condition
        // lookup
        data.results_by_name[result.name] = &result;
    }

    // Process define_results for config.h
    for (const CheckResult& result : define_results_) {
        // Use the define name from the check if available, otherwise use cache
        // variable name
        std::string define_name =
            result.define.has_value() ? *result.define : result.name;

        // Store result by define name for template replacement
        data.results_by_name[define_name] = &result;

        // Store define value
        data.define_values[define_name] = result.value.value_or("");

        // Drain builtin from set
        data.builtins.erase(define_name);
    }

    // Process subst_results for subst.h
    for (const CheckResult& result : subst_results_) {
        // Use the subst name from the check if available, otherwise use cache
        // variable name
        std::string subst_name =
            result.subst.has_value() ? *result.subst : result.name;

        // Store result by subst name for template replacement
        data.results_by_name[subst_name] = &result;

        // Store subst value
        data.subst_values[subst_name] = result.value.value_or("");

        // Drain builtin from set
        data.builtins.erase(subst_name);
    }

    return data;
}

// Step 2: Process defines replacement (if in defines/all mode)
std::string SourceGenerator::process_defines_replacement(
    std::string content, const ProcessedData& data) const {
    if (mode_ != Mode::kDefines && mode_ != Mode::kAll) {
        return content;
    }

    // Build a replacement map for all defines in a single pass
    std::unordered_map<std::string, UndefReplacement> replacements;

    for (const CheckResult& result : define_results_) {
        std::string define_name =
            result.define.has_value() ? *result.define : result.name;

        // Determine whether to create a #define or comment out the #undef
        bool should_create_define = false;
        if (result.type == CheckType::kDefine ||
            result.type == CheckType::kDecl) {
            // For AC_DEFINE and AC_CHECK_DECL:
            // - success=true → always create define
            // - success=false but value set → create define (e.g., if_false=0)
            // - success=false and no value → comment out (/* #undef */)
            should_create_define = result.success || result.value.has_value();
        } else {
            // For other types (kCompile, etc.), only create if success and
            // value is set
            should_create_define = result.success && result.value.has_value() &&
                                   !result.value->empty();
        }

        if (should_create_define) {
            std::string replacement_text = "#define " + define_name;

            if (result.value.has_value() && !result.value->empty()) {
                std::string value = format_value_for_define(*result.value);
                replacement_text += " " + value;
            } else {
                if (result.unquote) {
                    replacement_text += " ";
                } else {
                    replacement_text += " /**/";
                }
            }
            replacements[define_name] = {replacement_text, false};
        } else {
            replacements[define_name] = {"", true};
        }
    }

    // Add builtins (PACKAGE_* defines) to the replacement map
    for (const std::string& builtin : data.builtins) {
        std::string value{};
        std::map<std::string, std::string>::const_iterator value_it =
            data.define_values.find(builtin);
        if (value_it != data.define_values.end()) {
            value = value_it->second;
        }

        std::string replacement_text = "#define " + builtin;
        if (!value.empty()) {
            std::string processed_value = format_value_for_define(value);
            replacement_text += " " + processed_value;
        } else {
            replacement_text += " \"\"";
        }
        replacements[builtin] = {replacement_text, false};
    }

    // Single pass: replace all known #undefs and comment out any remaining
    return batch_replace_undefs(content, replacements, true);
}

// Step 3: Process subst replacements (if in subst/all mode)
std::string SourceGenerator::process_subst_replacements(
    std::string content, const ProcessedData& data) const {
    if (mode_ != Mode::kSubst && mode_ != Mode::kAll) {
        return content;
    }

    // Build a lookup map of @VAR@ → replacement value for single-pass
    // substitution
    std::unordered_map<std::string, std::string> subst_map;

    for (const CheckResult& result : subst_results_) {
        std::string subst_name =
            result.subst.has_value() ? *result.subst : result.name;

        std::map<std::string, std::string>::const_iterator subst_it =
            data.subst_values.find(subst_name);
        std::string subst_value = (subst_it != data.subst_values.end())
                                      ? subst_it->second
                                      : result.value.value_or("");
        subst_map[subst_name] = format_value_for_subst(subst_value);
    }

    // Add remaining builtins
    for (const std::string& builtin : data.builtins) {
        std::map<std::string, std::string>::const_iterator value_it =
            data.subst_values.find(builtin);
        if (value_it != data.subst_values.end() && !value_it->second.empty()) {
            subst_map[builtin] = format_value_for_subst(value_it->second);
        }
    }

    // Single pass: scan for @...@ patterns and replace from map
    std::string output;
    output.reserve(content.size());
    size_t i = 0;
    size_t len = content.size();

    while (i < len) {
        if (content[i] == '@') {
            // Look for closing @
            size_t end = content.find('@', i + 1);
            if (end != std::string::npos) {
                // Extract the name between @ delimiters
                size_t name_len = end - i - 1;
                if (name_len > 0) {
                    std::string name = content.substr(i + 1, name_len);
                    // Validate it's a C identifier
                    bool valid =
                        std::isalpha(static_cast<unsigned char>(name[0])) ||
                        name[0] == '_';
                    for (size_t j = 1; valid && j < name.size(); ++j) {
                        if (!std::isalnum(
                                static_cast<unsigned char>(name[j])) &&
                            name[j] != '_') {
                            valid = false;
                        }
                    }
                    if (valid) {
                        std::unordered_map<std::string,
                                           std::string>::const_iterator it =
                            subst_map.find(name);
                        if (it != subst_map.end()) {
                            output += it->second;
                            i = end + 1;
                            continue;
                        }
                    }
                }
            }
        }
        output += content[i];
        ++i;
    }

    return output;
}

// Step 4b: Comment out #undef statements for defines in subst mode
std::string SourceGenerator::comment_out_define_undefs(
    std::string content, const ProcessedData& /* data */) const {
    // Build a map of all define names → comment out
    std::unordered_map<std::string, UndefReplacement> replacements;
    for (const CheckResult& result : define_results_) {
        std::string define_name =
            result.define.has_value() ? *result.define : result.name;
        replacements[define_name] = {"", true};
    }

    return batch_replace_undefs(content, replacements, false);
}

// Step 4: Process inlines and direct substitutions
std::string SourceGenerator::process_inlines_and_direct_subst(
    std::string content,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions) const {
    // Process direct substitutions first (exact text replacement)
    for (const auto& [search_text, replacement] : substitutions) {
        size_t pos = 0;
        while ((pos = content.find(search_text, pos)) != std::string::npos) {
            content.replace(pos, search_text.length(), replacement);
            pos += replacement.length();
        }
    }

    // Process inline replacements
    for (const auto& [search_string, file_path] : inlines) {
        std::ifstream inline_file(file_path);
        if (!inline_file.is_open()) {
            throw std::runtime_error("Failed to open inline file: " +
                                     file_path.string());
        }
        std::stringstream buffer{};
        buffer << inline_file.rdbuf();
        std::string replacement_content = buffer.str();
        inline_file.close();

        // Find all positions of the search string
        std::vector<size_t> positions{};
        size_t pos = 0;
        while ((pos = content.find(search_string, pos)) != std::string::npos) {
            positions.push_back(pos);
            pos += search_string.length();
        }

        // Replace from the end backwards to avoid position shifts
        for (auto it = positions.rbegin(); it != positions.rend(); ++it) {
            content.replace(*it, search_string.length(), replacement_content);
        }
    }

    return content;
}

// Step 5: Clean up end of file
std::string SourceGenerator::cleanup_end_of_file(
    const std::string& content) const {
    std::string final_content{};
    final_content.reserve(content.size());
    std::istringstream stream(content);
    std::string line{};
    bool first_line = true;

    while (std::getline(stream, line)) {
        // Check if this line is a "#define NAME /**/" or "#define NAME "
        // pattern that should preserve trailing whitespace.
        bool preserve_trailing = false;
        if (line.size() >= 8 && line.compare(0, 8, "#define ") == 0) {
            // Find where the identifier ends (skip #define + spaces + name)
            size_t name_start = 8;
            while (name_start < line.size() && line[name_start] == ' ') {
                ++name_start;
            }
            // Verify there is an identifier
            if (name_start < line.size() &&
                (std::isalpha(static_cast<unsigned char>(line[name_start])) ||
                 line[name_start] == '_')) {
                size_t name_end = name_start + 1;
                while (
                    name_end < line.size() &&
                    (std::isalnum(static_cast<unsigned char>(line[name_end])) ||
                     line[name_end] == '_')) {
                    ++name_end;
                }
                // After identifier, check what remains
                if (name_end < line.size()) {
                    std::string suffix = line.substr(name_end);
                    // "#define NAME /**/" - empty AC_DEFINE value
                    if (suffix == " /**/") {
                        preserve_trailing = true;
                    }
                    // "#define NAME " - empty define with trailing space
                    // (all remaining chars are whitespace)
                    if (!preserve_trailing) {
                        bool all_ws = true;
                        for (char c : suffix) {
                            if (c != ' ' && c != '\t') {
                                all_ws = false;
                                break;
                            }
                        }
                        if (all_ws) {
                            preserve_trailing = true;
                        }
                    }
                }
            }
        }

        // Strip trailing whitespace unless this is a preserved define pattern
        if (!preserve_trailing) {
            while (!line.empty() &&
                   (line.back() == ' ' || line.back() == '\t')) {
                line.pop_back();
            }
        }

        if (!first_line) {
            final_content += '\n';
        }
        final_content += line;
        first_line = false;
    }

    // If original content ended with a newline, add it back
    if (!content.empty() && content.back() == '\n') {
        final_content += '\n';
    }

    return final_content;
}

// Value formatting helpers
std::string SourceGenerator::format_value_for_subst(
    const std::string& value) const {
    // Empty value should remain empty
    if (value.empty()) {
        return "";
    }

    // Try to parse the value as JSON to extract the actual value
    try {
        nlohmann::json parsed = nlohmann::json::parse(value);

        if (parsed.is_string()) {
            // String: return the unescaped string value
            return parsed.get<std::string>();
        } else if (parsed.is_null()) {
            // Null: return empty string
            return "";
        } else {
            // Number, boolean, or other types: return as string representation
            return parsed.dump();
        }
    } catch (const nlohmann::json::parse_error&) {
        // If parsing fails, treat as plain string (for backward compatibility)
        return value;
    }
}

std::string SourceGenerator::format_value_for_define(
    const std::string& value) const {
    // Empty value should remain empty
    if (value.empty()) {
        return "";
    }

    // Try to parse the value as JSON to determine the original type
    try {
        nlohmann::json parsed = nlohmann::json::parse(value);

        if (parsed.is_number()) {
            // Number: render as unquoted (e.g., 1, 42, 2025)
            return parsed.dump();
        } else if (parsed.is_boolean()) {
            // Boolean: render as C boolean literal (unquoted)
            std::string bool_str = parsed.get<bool>() ? "true" : "false";
            return bool_str;
        } else if (parsed.is_string()) {
            // String: render as-is (no outer quotes added)
            // JSON parsing automatically handles escaped quotes:
            // - "\"foo\"" becomes the string "foo" (with quotes as content)
            // - "foo" becomes the string foo (no quotes)
            std::string str_value = parsed.get<std::string>();

            // Return string value as-is (no quotes added, no special handling)
            return str_value;
        } else if (parsed.is_null()) {
            // Null: should not happen (handled in from_json), but return empty
            return "";
        } else {
            // Other JSON types: use dump() (no quotes added)
            return parsed.dump();
        }
    } catch (const nlohmann::json::parse_error&) {
        // If parsing fails, treat as plain string
        // This handles values that aren't JSON-encoded
        {
            // Check if it's a number
            bool is_number = false;
            if (!value.empty()) {
                bool has_dot = false;
                bool has_digit = false;
                bool is_valid = true;
                for (size_t i = 0; i < value.size(); ++i) {
                    char c = value[i];
                    if (std::isdigit(static_cast<unsigned char>(c))) {
                        has_digit = true;
                    } else if (c == '.' && !has_dot && i > 0 &&
                               i < value.size() - 1) {
                        has_dot = true;
                    } else if ((c == '-' || c == '+') && i == 0) {
                        // Allow leading sign
                    } else {
                        is_valid = false;
                        break;
                    }
                }
                is_number = is_valid && has_digit;
            }

            if (is_number) {
                return value;
            } else {
                // Plain string: return as-is (no quotes added)
                return value;
            }
        }
    }
}

}  // namespace rules_cc_autoconf

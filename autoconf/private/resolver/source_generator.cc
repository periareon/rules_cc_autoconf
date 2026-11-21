#include "autoconf/private/resolver/source_generator.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <optional>
#include <sstream>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/common/file_util.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

namespace {

/**
 * @brief What a template directive for one name becomes: its definition after
 * `define ` (e.g., "FOO 1"), or nullopt if the name is undefined.
 */
using Definition = std::optional<std::string>;

/**
 * @brief Extract the bare identifier from a define name, stripping any
 * function-like macro argument list.
 *
 * `AC_DEFINE([FOO(a, b)], ...)` writes `#undef FOO` into config.h.in but
 * emits `#define FOO(a, b) ...` in the generated config.h — so the lookup
 * key must be the bare "FOO" even when the emitted line carries the full
 * "FOO(a, b)".
 */
std::string strip_macro_args(const std::string& define_name) {
    size_t paren = define_name.find('(');
    if (paren == std::string::npos) {
        return define_name;
    }
    size_t last = define_name.find_last_not_of(" \t", paren - 1);
    return last == std::string::npos ? std::string()
                                     : define_name.substr(0, last + 1);
}

/**
 * @brief A `#define` or `#undef` template directive.
 */
struct Directive {
    std::string prefix;  ///< Everything before the keyword ("  #  ").
    bool is_undef;       ///< `#undef` rather than `#define`.
    std::string name;    ///< The macro name, without any parameter list.
};

/**
 * @brief Match a template directive the way config.status does.
 *
 * config.status rewrites lines matching
 * `^[\t ]*#[\t ]*(define|undef)[\t ]+NAME([\t (]|$)`, so a directive must start
 * its line and its name must end the line or be followed by a blank or `(`.
 *
 * @param line One line of the template, without its `\n`.
 * @return The directive, or nullopt if the line is not one.
 */
std::optional<Directive> match_directive(std::string_view line) {
    size_t i = 0;
    auto is_blank = [&line](size_t pos) {
        return pos < line.size() && (line[pos] == ' ' || line[pos] == '\t');
    };
    while (is_blank(i)) {
        ++i;
    }
    if (i >= line.size() || line[i] != '#') {
        return std::nullopt;
    }
    ++i;
    while (is_blank(i)) {
        ++i;
    }

    size_t keyword = i;
    bool is_undef = false;
    if (line.substr(i, 6) == "define") {
        i += 6;
    } else if (line.substr(i, 5) == "undef") {
        is_undef = true;
        i += 5;
    } else {
        return std::nullopt;
    }
    if (!is_blank(i)) {
        return std::nullopt;
    }
    while (is_blank(i)) {
        ++i;
    }

    size_t name_start = i;
    if (i >= line.size() ||
        (!std::isalpha(static_cast<unsigned char>(line[i])) &&
         line[i] != '_')) {
        return std::nullopt;
    }
    while (
        i < line.size() &&
        (std::isalnum(static_cast<unsigned char>(line[i])) || line[i] == '_')) {
        ++i;
    }
    if (i < line.size() && !is_blank(i) && line[i] != '(') {
        return std::nullopt;
    }

    return Directive{std::string(line.substr(0, keyword)), is_undef,
                     std::string(line.substr(name_start, i - name_start))};
}

/**
 * @brief Rewrite every template directive in content.
 *
 * A directive for a name with a definition becomes that definition, keeping
 * the whitespace around the `#` and dropping the rest of the line, whether it
 * was `#define` or `#undef`. Otherwise an `#undef` is commented out and a
 * `#define` is kept. Names missing from replacements are treated as undefined
 * when comment_remaining is set and left alone when it is not.
 */
std::string replace_directives(
    const std::string& content,
    const std::unordered_map<std::string, Definition>& replacements,
    bool comment_remaining) {
    std::string output;
    output.reserve(content.size());

    size_t start = 0;
    while (start < content.size()) {
        size_t end = content.find('\n', start);
        bool terminated = end != std::string::npos;
        if (!terminated) {
            end = content.size();
        }
        std::string_view line(content.data() + start, end - start);

        std::optional<Directive> directive = match_directive(line);
        std::unordered_map<std::string, Definition>::const_iterator it =
            directive ? replacements.find(directive->name) : replacements.end();
        bool known = it != replacements.end();
        if (known && it->second) {
            output += directive->prefix + "define " + *it->second;
        } else if (directive && directive->is_undef &&
                   (known || comment_remaining)) {
            output +=
                "/* " + directive->prefix + "undef " + directive->name + " */";
        } else {
            output += line;
        }
        if (terminated) {
            output += '\n';
        }
        start = end + 1;
    }

    return output;
}

/// Starts each directive line a substitution writes, so that no rewrite
/// matches it; process_template removes it before writing the header.
constexpr char kSubstituted = '\x01';

/**
 * @brief Mark the directive lines of a substitution's value.
 *
 * A directive a substitution writes is the user's, not the template's, so it
 * is kept as written.
 */
std::string mark_directives(const std::string& value) {
    std::string marked;
    size_t start = 0;
    while (true) {
        size_t end = value.find('\n', start);
        std::string_view line(
            value.data() + start,
            (end == std::string::npos ? value.size() : end) - start);
        if (match_directive(line)) {
            marked += kSubstituted;
        }
        marked += line;
        if (end == std::string::npos) {
            return marked;
        }
        marked += '\n';
        start = end + 1;
    }
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

    // A subst file keeps the template's last line as it was.
    bool template_has_trailing_newline =
        !template_content.empty() && template_content.back() == '\n';
    if (mode_ == Mode::kSubst && !template_has_trailing_newline) {
        while (!content.empty() && content.back() == '\n') {
            content.pop_back();
        }
    }

    std::ofstream file =
        open_ofstream(output_path, std::ios::out | std::ios::binary);
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
    } else if (!content.empty() && content.back() != '\n') {
        // config.status prints every line of a header with a newline.
        content += '\n';
    }
    content.erase(std::remove(content.begin(), content.end(), kSubstituted),
                  content.end());

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

    // Process define_results for config.h. Key everything by the bare
    // identifier so downstream `#undef NAME` lookups match even when the
    // define is function-like (e.g., "PRINTF_FORMAT(a, b)").
    data.define_entries.reserve(define_results_.size());
    for (const CheckResult& result : define_results_) {
        std::string define_name =
            result.define.has_value() ? *result.define : result.name;
        std::string lookup_key = strip_macro_args(define_name);

        data.results_by_name[lookup_key] = &result;
        data.define_values[lookup_key] = result.value.value_or("");
        data.builtins.erase(lookup_key);
        data.define_entries.emplace_back(std::move(lookup_key), &result);
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
    std::unordered_map<std::string, Definition> replacements;

    for (const auto& [lookup_key, result_ptr] : data.define_entries) {
        const CheckResult& result = *result_ptr;
        const std::string& define_name =
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
            std::string replacement_text = define_name;

            if (result.value.has_value() && !result.value->empty()) {
                std::string value =
                    format_value_for_define(*result.value, result.unquote);
                replacement_text += " " + value;
            } else if (!result.unquote) {
                // AC_DEFINE with an empty value renders as `#define FOO /**/`.
                // AC_DEFINE_UNQUOTED with an empty value renders as a bare
                // `#define FOO` (autoconf 2.72+ no longer emits a trailing
                // space; the GNU conformance tests pin autoconf 2.73).
                replacement_text += " /**/";
            }
            replacements[lookup_key] = replacement_text;
        } else {
            replacements[lookup_key] = std::nullopt;
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

        std::string replacement_text = builtin;
        if (!value.empty()) {
            std::string processed_value = format_value_for_define(value);
            replacement_text += " " + processed_value;
        } else {
            replacement_text += " \"\"";
        }
        replacements[builtin] = replacement_text;
    }

    return replace_directives(content, replacements, true);
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

    // Single pass: scan for @...@ patterns and replace from map.  Comments
    // are tracked so that a value spanning lines (a system header inlined for
    // @NEXT_<hdr>_H@ where #include_next is unavailable) never lands inside
    // one: gnulib's stdint.in.h mentions @NEXT_STDINT_H@ in a comment, and
    // the header's first `*/` would end that comment early.  Single-line
    // values are substituted everywhere, as config.status does.
    std::string output;
    output.reserve(content.size());
    size_t i = 0;
    size_t len = content.size();
    bool in_block_comment = false;
    bool in_line_comment = false;

    while (i < len) {
        if (in_block_comment) {
            if (content[i] == '*' && i + 1 < len && content[i + 1] == '/') {
                in_block_comment = false;
                output += "*/";
                i += 2;
                continue;
            }
        } else if (in_line_comment) {
            if (content[i] == '\n') {
                in_line_comment = false;
            }
        } else if (content[i] == '/' && i + 1 < len && content[i + 1] == '*') {
            in_block_comment = true;
            output += "/*";
            i += 2;
            continue;
        } else if (content[i] == '/' && i + 1 < len && content[i + 1] == '/') {
            in_line_comment = true;
        }
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
                        bool in_comment = in_block_comment || in_line_comment;
                        if (it != subst_map.end() &&
                            !(in_comment &&
                              it->second.find('\n') != std::string::npos)) {
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
    std::string content, const ProcessedData& data) const {
    // Build a map of all define names → comment out
    std::unordered_map<std::string, Definition> replacements;
    for (const auto& [lookup_key, _] : data.define_entries) {
        replacements[lookup_key] = std::nullopt;
    }

    return replace_directives(content, replacements, false);
}

// Step 4: Process inlines and direct substitutions
std::string SourceGenerator::process_inlines_and_direct_subst(
    std::string content,
    const std::map<std::string, std::filesystem::path>& inlines,
    const std::map<std::string, std::string>& substitutions) const {
    // Process direct substitutions first (exact text replacement)
    for (const auto& [search_text, value] : substitutions) {
        std::string replacement = mark_directives(value);
        size_t pos = 0;
        while ((pos = content.find(search_text, pos)) != std::string::npos) {
            content.replace(pos, search_text.length(), replacement);
            pos += replacement.length();
        }
    }

    // Process inline replacements
    for (const auto& [search_string, file_path] : inlines) {
        std::ifstream inline_file = open_ifstream(file_path);
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
        // Check if this line is a "#define NAME " pattern that should
        // preserve trailing whitespace.
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
                preserve_trailing = name_end < line.size() &&
                                    line.find_first_not_of(" \t", name_end) ==
                                        std::string::npos;
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

std::string SourceGenerator::format_value_for_define(const std::string& value,
                                                     bool unquote) const {
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

            if (unquote) {
                // AC_DEFINE_UNQUOTED: strip surrounding quotes
                if (str_value.size() >= 2 && str_value.front() == '"' &&
                    str_value.back() == '"') {
                    return str_value.substr(1, str_value.size() - 2);
                }
            }

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

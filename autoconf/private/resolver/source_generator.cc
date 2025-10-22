#include "autoconf/private/resolver/source_generator.h"

#include <fstream>
#include <regex>
#include <sstream>

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

SourceGenerator::SourceGenerator(const Config& config,
                                 const std::vector<CheckResult>& results)
    : config_(config), results_(results) {}

void SourceGenerator::generate_config_header(
    const std::filesystem::path& output_path,
    const std::string& template_content) {
    std::string content = process_template(template_content);

    std::ofstream file(output_path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open output file: " +
                                 output_path.string());
    }

    file << content;
    file.close();
}

std::string SourceGenerator::process_template(
    const std::string& template_content) {
    std::string content = template_content;

    // Replace package info placeholders and #undef patterns
    if (!config_.package_name.empty()) {
        // Replace @PACKAGE_NAME@ placeholders
        std::regex package_name_regex("@PACKAGE_NAME@");
        content = std::regex_replace(content, package_name_regex,
                                     config_.package_name);

        // Replace #undef PACKAGE_NAME patterns (preserve all trailing newlines)
        content = replace_undef(
            content, "PACKAGE_NAME",
            "#define PACKAGE_NAME \"" + config_.package_name + "\"");

        // Replace #undef PACKAGE_TARNAME patterns (preserve all trailing
        // newlines)
        content = replace_undef(
            content, "PACKAGE_TARNAME",
            "#define PACKAGE_TARNAME \"" + config_.package_name + "\"");
    }

    if (!config_.package_version.empty()) {
        // Replace @PACKAGE_VERSION@ placeholders
        std::regex package_version_regex("@PACKAGE_VERSION@");
        content = std::regex_replace(content, package_version_regex,
                                     config_.package_version);

        // Replace #undef PACKAGE_VERSION patterns (preserve all trailing
        // newlines)
        content = replace_undef(
            content, "PACKAGE_VERSION",
            "#define PACKAGE_VERSION \"" + config_.package_version + "\"");
    }

    // Replace #undef PACKAGE_STRING pattern (preserve all trailing newlines)
    if (!config_.package_name.empty() && !config_.package_version.empty()) {
        std::string package_string =
            config_.package_name + " " + config_.package_version;
        content =
            replace_undef(content, "PACKAGE_STRING",
                          "#define PACKAGE_STRING \"" + package_string + "\"");
    }

    // Replace #undef PACKAGE_BUGREPORT and PACKAGE_URL with empty defaults
    // (preserve all trailing newlines)
    content = replace_undef(content, "PACKAGE_BUGREPORT",
                            "#define PACKAGE_BUGREPORT \"\"");
    content = replace_undef(content, "PACKAGE_URL", "#define PACKAGE_URL \"\"");

    // Replace custom defines
    for (std::map<std::string, std::string>::const_iterator it =
             config_.defines.begin();
         it != config_.defines.end(); ++it) {
        std::regex defineRegex("@" + it->first + "@");
        content = std::regex_replace(content, defineRegex, it->second);
    }

    // Replace check results
    for (const CheckResult& result : results_) {
        // Replace @DEFINE@ patterns
        std::regex definePattern("@" + result.define + "@");
        content = std::regex_replace(content, definePattern, result.value);

        // Replace #undef DEFINE patterns (preserve all trailing newlines)
        std::string replacement_text{};
        if (result.success) {
            replacement_text = "#define " + result.define;
            if (!result.value.empty()) {
                replacement_text += " " + result.value;
            }
        } else {
            replacement_text = "";  // placeholder, will use is_comment flag
        }

        if (result.success) {
            content =
                replace_undef(content, result.define, replacement_text, false);
        } else {
            content = replace_undef(content, result.define, "", true);
        }
    }

    return content;
}

std::string SourceGenerator::generate_default_template() const {
    std::ostringstream template_content;

    // Generation comment
    template_content
        << "/* config.h.  Generated by Bazel rules_cc_autoconf.  */\n";
    template_content << "\n";

    // Add #undef statements for all check results (without comments - they'll
    // be added during processing if needed)
    for (const CheckResult& result : results_) {
        template_content << "#undef " << result.define << "\n";
    }

    if (!results_.empty()) {
        template_content << "\n";
    }

    // Add package info
    template_content
        << "/* Define to the address where bug reports for this package "
           "should be sent. */\n";
    template_content << "#undef PACKAGE_BUGREPORT\n";
    template_content << "\n";

    if (!config_.package_name.empty()) {
        template_content << "/* Define to the full name of this package. */\n";
        template_content << "#undef PACKAGE_NAME\n";
        template_content << "\n";

        template_content
            << "/* Define to the full name and version of this package. */\n";
        template_content << "#undef PACKAGE_STRING\n";
        template_content << "\n";

        template_content
            << "/* Define to the one symbol short name of this package. */\n";
        template_content << "#undef PACKAGE_TARNAME\n";
        template_content << "\n";

        template_content << "/* Define to the home page for this package. */\n";
        template_content << "#undef PACKAGE_URL\n";
        template_content << "\n";
    }

    if (!config_.package_version.empty()) {
        template_content << "/* Define to the version of this package. */\n";
        template_content << "#undef PACKAGE_VERSION\n";
    }

    return template_content.str();
}

}  // namespace rules_cc_autoconf

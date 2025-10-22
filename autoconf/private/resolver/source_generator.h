#pragma once

#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/config.h"

namespace rules_cc_autoconf {

/**
 * @brief Generates config.h header files from check results.
 *
 * Can generate headers from scratch or process template files (config.h.in)
 * by substituting placeholders with check results and package information.
 */
class SourceGenerator {
   public:
    /**
     * @brief Construct a SourceGenerator.
     * @param results Vector of check results to include in the header.
     */
    explicit SourceGenerator(const std::vector<CheckResult>& results);

    /**
     * @brief Generate a config.h header file from a template string.
     * @param output_path Path where the config.h file will be written.
     * @param template_content Template content as a string (with @PLACEHOLDER@
     * markers and #undef statements).
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @throws std::runtime_error if the file cannot be opened for writing.
     */
    void generate_config_header(
        const std::filesystem::path& output_path,
        const std::string& template_content,
        const std::map<std::string, std::filesystem::path>& inlines = {});

    // Deleted copy and move assignment operators (const reference members)
    SourceGenerator& operator=(const SourceGenerator&) = delete;
    SourceGenerator& operator=(SourceGenerator&&) = delete;

   private:
    const std::vector<CheckResult>& results_{};  ///< Reference to check results

    /**
     * @brief Process a template string, substituting placeholders.
     * @param template_content Template content with @PLACEHOLDER@ markers.
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @return Processed content with placeholders replaced.
     */
    std::string process_template(
        const std::string& template_content,
        const std::map<std::string, std::filesystem::path>& inlines = {});
};

}  // namespace rules_cc_autoconf

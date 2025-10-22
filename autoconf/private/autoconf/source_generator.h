#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "autoconf/private/autoconf/check_result.h"
#include "autoconf/private/autoconf/config.h"

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
     * @brief Construct a ConfigGenerator.
     * @param config Configuration containing package info and custom defines.
     * @param results Vector of check results to include in the header.
     */
    explicit SourceGenerator(const Config& config,
                             const std::vector<CheckResult>& results);

    /**
     * @brief Generate a config.h header file from a template string.
     * @param output_path Path where the config.h file will be written.
     * @param template_content Template content as a string (with @PLACEHOLDER@
     * markers and #undef statements).
     * @throws std::runtime_error if the file cannot be opened for writing.
     */
    void generate_config_header(const std::filesystem::path& output_path,
                                const std::string& template_content);

    /**
     * @brief Generate a default template string.
     * @return Default template content as a string with #undef statements for
     * all checks.
     */
    std::string generate_default_template() const;

    // Deleted copy and move assignment operators (const reference members)
    SourceGenerator& operator=(const SourceGenerator&) = delete;
    SourceGenerator& operator=(SourceGenerator&&) = delete;

   private:
    const Config& config_;                       ///< Reference to configuration
    const std::vector<CheckResult>& results_{};  ///< Reference to check results

    /**
     * @brief Process a template string, substituting placeholders.
     * @param template_content Template content with @PLACEHOLDER@ markers.
     * @return Processed content with placeholders replaced.
     */
    std::string process_template(const std::string& template_content);
};

}  // namespace rules_cc_autoconf

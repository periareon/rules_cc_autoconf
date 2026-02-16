#pragma once

#include <filesystem>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/config.h"

namespace rules_cc_autoconf {

/**
 * @brief Processing mode for header generation.
 */
enum class Mode {
    kDefines,  ///< Process only defines (not subst)
    kSubst,    ///< Process only substitution variables
    kAll,      ///< Process both defines and substitution variables
};

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
     * @param cache_results Vector of cache variable check results.
     * @param define_results Vector of define check results.
     * @param subst_results Vector of subst check results.
     * @param mode Processing mode (default: kDefines).
     */
    explicit SourceGenerator(const std::vector<CheckResult>& cache_results,
                             const std::vector<CheckResult>& define_results,
                             const std::vector<CheckResult>& subst_results,
                             Mode mode = Mode::kDefines);

    /**
     * @brief Generate a config.h header file from a template string.
     * @param output_path Path where the config.h file will be written.
     * @param template_content Template content as a string (with @PLACEHOLDER@
     * markers and #undef statements).
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @param substitutions Map from placeholder names to values for direct
     * @VAR@ substitution.
     * @throws std::runtime_error if the file cannot be opened for writing.
     */
    void generate_config_header(
        const std::filesystem::path& output_path,
        const std::string& template_content,
        const std::map<std::string, std::filesystem::path>& inlines = {},
        const std::map<std::string, std::string>& substitutions = {});

    // Deleted copy and move assignment operators (const reference members)
    SourceGenerator& operator=(const SourceGenerator&) = delete;
    SourceGenerator& operator=(SourceGenerator&&) = delete;

   private:
    const std::vector<CheckResult>&
        cache_results_{};  ///< Reference to cache variable results
    const std::vector<CheckResult>&
        define_results_{};  ///< Reference to define results
    const std::vector<CheckResult>&
        subst_results_{};              ///< Reference to subst results
    const Mode mode_{Mode::kDefines};  ///< Processing mode

    /**
     * @brief Process a template string, substituting placeholders.
     * @param template_content Template content with @PLACEHOLDER@ markers.
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @param substitutions Map from placeholder names to values for direct
     * @VAR@ substitution.
     * @return Processed content with placeholders replaced.
     */
    std::string process_template(
        const std::string& template_content,
        const std::map<std::string, std::filesystem::path>& inlines = {},
        const std::map<std::string, std::string>& substitutions = {});

    // Helper functions for processing
    struct ProcessedData {
        std::map<std::string, std::string>
            define_values;  // Map from define name to value
        std::map<std::string, std::string>
            subst_values;  // Map from define name to subst value
        std::map<std::string, const CheckResult*>
            results_by_name;  // Map from define name to result
        std::set<std::string>
            builtins;  // Set of builtin names that need processing
    };

    /**
     * @brief Step 1: Load and parse all data from check results.
     * @return ProcessedData containing all parsed information.
     */
    ProcessedData load_and_parse_data() const;

    /**
     * @brief Step 2: Process defines replacement (if in defines/all mode).
     * @param content Template content to process.
     * @param data Processed data from step 1.
     * @return Content with #undef replaced by #define and remaining #undef
     * commented out.
     */
    std::string process_defines_replacement(std::string content,
                                            const ProcessedData& data) const;

    /**
     * @brief Step 3: Process subst replacements (if in subst/all mode).
     * @param content Template content to process.
     * @param data Processed data from step 1.
     * @return Content with @VAR@ placeholders replaced.
     */
    std::string process_subst_replacements(std::string content,
                                           const ProcessedData& data) const;

    /**
     * @brief Comment out #undef statements for defines in subst mode.
     * @param content Template content to process.
     * @param data Processed data from step 1.
     * @return Content with #undef statements for defines commented out.
     */
    std::string comment_out_define_undefs(std::string content,
                                          const ProcessedData& data) const;

    /**
     * @brief Step 4: Process inlines and direct substitutions.
     * @param content Template content to process.
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @param substitutions Map from placeholder names to values for direct
     * substitution.
     * @return Content with inlines and direct substitutions applied.
     */
    std::string process_inlines_and_direct_subst(
        std::string content,
        const std::map<std::string, std::filesystem::path>& inlines,
        const std::map<std::string, std::string>& substitutions) const;

    /**
     * @brief Step 5: Clean up end of file (strip trailing whitespace).
     * @param content Content to clean up.
     * @return Content with trailing whitespace stripped.
     */
    std::string cleanup_end_of_file(const std::string& content) const;

    // Value formatting helpers
    std::string format_value_for_subst(const std::string& value) const;
    std::string format_value_for_define(const std::string& value) const;
};

}  // namespace rules_cc_autoconf

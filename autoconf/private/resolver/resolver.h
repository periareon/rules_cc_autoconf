#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "autoconf/private/resolver/source_generator.h"

namespace rules_cc_autoconf {

/**
 * @brief Library for resolving autoconf results and generating headers.
 *
 * Merges check results from multiple sources, optionally runs additional checks
 * from templates, logs all check results, and generates config.h headers.
 */
class Resolver {
   public:
    /**
     * @brief Resolve results and generate a header file.
     *
     * Loads and merges check results from multiple JSON files, logs all check
     * results in order, and generates a config.h header file from a template.
     *
     * @param cache_results_paths Paths to JSON result files for cache
     * variables.
     * @param define_results_paths Paths to JSON result files for defines.
     * @param subst_results_paths Paths to JSON result files for subst values.
     * @param template_path Path to template file (config.h.in) (required).
     * @param output_path Path where config.h will be written.
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @param substitutions Map from placeholder names to values for direct
     * @VAR@ substitution.
     * @param mode Processing mode (default: kDefines).
     * @return 0 on success, 1 on error.
     */
    static int resolve_and_generate(
        const std::vector<std::filesystem::path>& cache_results_paths,
        const std::vector<std::filesystem::path>& define_results_paths,
        const std::vector<std::filesystem::path>& subst_results_paths,
        const std::filesystem::path& template_path,
        const std::filesystem::path& output_path,
        const std::map<std::string, std::filesystem::path>& inlines = {},
        const std::map<std::string, std::string>& substitutions = {},
        Mode mode = Mode::kDefines);
};

}  // namespace rules_cc_autoconf

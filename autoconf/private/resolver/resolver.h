#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

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
     * Loads and merges check results from multiple JSON files, optionally runs
     * additional checks from templates, logs all check results in order, and
     * generates a config.h header file.
     *
     * @param results_paths Paths to JSON result files to merge.
     * @param package_info_path Path to JSON package info file.
     * @param template_path Optional path to template file (config.h.in).
     * @param config_path Optional path to config file (for running additional
     *                    checks from template).
     * @param output_path Path where config.h will be written.
     * @param output_results_path Optional path where merged results JSON will
     *                            be written.
     * @return 0 on success, 1 on error.
     */
    static int resolve_and_generate(
        const std::vector<std::filesystem::path>& results_paths,
        const std::filesystem::path& package_info_path,
        const std::optional<std::filesystem::path>& template_path,
        const std::optional<std::filesystem::path>& config_path,
        const std::filesystem::path& output_path,
        const std::optional<std::filesystem::path>& output_results_path =
            std::nullopt);
};

}  // namespace rules_cc_autoconf

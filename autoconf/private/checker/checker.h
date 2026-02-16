#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/check_runner.h"
#include "autoconf/private/checker/config.h"

namespace rules_cc_autoconf {

/**
 * @brief Mapping of lookup name to result file path.
 *
 * Format: --dep=lookup_name=file_path
 * Example: --dep=HAVE_FOO=/path/to/result.json
 *
 * The lookup_name can be a cache variable name, define name, or subst name.
 * Starlark is responsible for deduplication - any duplicate name is an error.
 */
struct DepMapping {
    std::string lookup_name;
    std::filesystem::path file_path;
};

/**
 * @brief Library for running autoconf checks.
 *
 * Provides a simple interface to run checks using a configuration.
 */
class Checker {
   public:
    /**
     * @brief Run a single check from a JSON file.
     * @param check_path Path to JSON file containing a single check.
     * @param config_path Path to JSON config file (for compiler info).
     * @param results_path Path where results JSON will be written.
     * @param dep_mappings Vector of name->file mappings for dependent check
     * results.
     * @return 0 on success, 1 on error.
     */
    static int run_check_from_file(const std::filesystem::path& check_path,
                                   const std::filesystem::path& config_path,
                                   const std::filesystem::path& results_path,
                                   const std::vector<DepMapping>& dep_mappings);
};

}  // namespace rules_cc_autoconf

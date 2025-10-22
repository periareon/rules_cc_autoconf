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
 * @brief Library for running autoconf checks.
 *
 * Provides a simple interface to run checks using a configuration.
 */
class Checker {
   public:
    /**
     * @brief Run checks from a config file and write results to a JSON file.
     * @param config_path Path to JSON config file.
     * @param results_path Path where results JSON will be written.
     * @return 0 on success, 1 on error.
     */
    static int run_checks(const std::filesystem::path& config_path,
                          const std::filesystem::path& results_path);

    /**
     * @brief Run a single check.
     * @param config_path Path to JSON config file.
     * @param check_index Index of check in config to run.
     * @param results_path Path where results JSON will be written (single
     * result).
     * @return 0 on success, 1 on error.
     */
    static int run_single_check(const std::filesystem::path& config_path,
                                size_t check_index,
                                const std::filesystem::path& results_path);

    /**
     * @brief Run multiple checks.
     * @param config_path Path to JSON config file.
     * @param check_indices Indices of checks in config to run.
     * @param results_path Path where results JSON will be written.
     * @return 0 on success, 1 on error.
     */
    static int run_checks(const std::filesystem::path& config_path,
                          const std::vector<size_t>& check_indices,
                          const std::filesystem::path& results_path);
};

}  // namespace rules_cc_autoconf

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
     * @brief Run checks by define names.
     * @param config_path Path to JSON config file.
     * @param check_defines Define names of checks to run.
     * @param results_path Path where results JSON will be written.
     * @param required_results Optional vector of paths to JSON files containing
     * results from other checks (for requirement validation).
     * @return 0 on success, 1 on error.
     */
    static int run_checks_by_define(
        const std::filesystem::path& config_path,
        const std::vector<std::string>& check_defines,
        const std::filesystem::path& results_path,
        const std::vector<std::filesystem::path>& required_results = {});
};

}  // namespace rules_cc_autoconf

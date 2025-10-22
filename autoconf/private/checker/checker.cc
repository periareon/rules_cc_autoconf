#include "autoconf/private/checker/checker.h"

#include <fstream>
#include <iostream>

#include "autoconf/private/checker/config.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

int Checker::run_checks(const std::filesystem::path& config_path,
                        const std::filesystem::path& results_path) {
    try {
        std::unique_ptr<Config> config = Config::from_file(config_path);

        CheckRunner runner(*config);
        std::vector<CheckResult> results = runner.run_all_checks();

        // Write results to JSON
        nlohmann::json j = nlohmann::json::object();
        for (const CheckResult& r : results) {
            j[r.define] = {
                {"value", r.value},
                {"success", r.success},
            };
        }

        std::ofstream results_file(results_path);
        if (!results_file.is_open()) {
            std::cerr << "Error: Failed to open results file: " << results_path
                      << std::endl;
            return 1;
        }
        results_file << j.dump(4) << std::endl;
        results_file.close();

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

int Checker::run_single_check(const std::filesystem::path& config_path,
                              size_t check_index,
                              const std::filesystem::path& results_path) {
    try {
        std::unique_ptr<Config> config = Config::from_file(config_path);

        if (check_index >= config->checks.size()) {
            std::cerr << "Error: Check index " << check_index
                      << " is out of range (config has "
                      << config->checks.size() << " checks)" << std::endl;
            return 1;
        }

        CheckRunner runner(*config);
        CheckResult result = runner.run_check(config->checks[check_index]);

        // Write single result to JSON
        nlohmann::json j = nlohmann::json::object();
        j[result.define] = {
            {"value", result.value},
            {"success", result.success},
        };

        std::ofstream results_file(results_path);
        if (!results_file.is_open()) {
            std::cerr << "Error: Failed to open results file: " << results_path
                      << std::endl;
            return 1;
        }
        results_file << j.dump(4) << std::endl;
        results_file.close();

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

int Checker::run_checks(const std::filesystem::path& config_path,
                        const std::vector<size_t>& check_indices,
                        const std::filesystem::path& results_path) {
    try {
        std::unique_ptr<Config> config = Config::from_file(config_path);

        CheckRunner runner(*config);
        nlohmann::json j = nlohmann::json::object();

        for (size_t index : check_indices) {
            if (index >= config->checks.size()) {
                std::cerr << "Warning: Check index " << index
                          << " is out of range (config has "
                          << config->checks.size() << " checks), skipping"
                          << std::endl;
                continue;
            }

            CheckResult result = runner.run_check(config->checks[index]);
            j[result.define] = {
                {"value", result.value},
                {"success", result.success},
            };
        }

        std::ofstream results_file(results_path);
        if (!results_file.is_open()) {
            std::cerr << "Error: Failed to open results file: " << results_path
                      << std::endl;
            return 1;
        }
        results_file << j.dump(4) << std::endl;
        results_file.close();

        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }
}

}  // namespace rules_cc_autoconf

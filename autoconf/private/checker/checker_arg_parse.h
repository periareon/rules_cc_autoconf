#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Parsed command-line arguments for checker binary.
 */
struct CheckerArgs {
    /** Path to JSON config file */
    std::filesystem::path config_path{};

    /** Path to JSON results file to write */
    std::filesystem::path results_path{};

    /** Optional: run only checks with specific define names (if empty, run all)
     */
    std::vector<std::string> check_defines{};

    /** Optional: paths to JSON files containing results from other checks */
    std::vector<std::filesystem::path> required_results{};

    /** Whether to show help */
    bool show_help = false;
};

/**
 * @brief Command-line argument parser for checker binary.
 */
class CheckerArgParse {
   public:
    /**
     * @brief Parse command-line arguments.
     * @param argc Number of arguments.
     * @param argv Array of argument strings.
     * @return Parsed CheckerArgs struct, or std::nullopt if parsing failed.
     */
    static std::optional<CheckerArgs> parse(int argc, char* argv[]);

    /**
     * @brief Print usage information.
     * @param program_name Name of the program (argv[0]).
     */
    static void print_usage(const char* program_name);
};

}  // namespace rules_cc_autoconf

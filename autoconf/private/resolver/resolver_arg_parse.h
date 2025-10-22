#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Parsed command-line arguments for resolver binary.
 */
struct ResolverArgs {
    /** Paths to JSON result files to merge */
    std::vector<std::filesystem::path> results_paths{};

    /** Optional path to template file (config.h.in) */
    std::optional<std::filesystem::path> template_path{};

    /** Path to output config.h file */
    std::filesystem::path output_path{};

    /** Inline replacements: map from search string to file path */
    std::map<std::string, std::filesystem::path> inlines{};

    /** Whether to show help */
    bool show_help = false;
};

/**
 * @brief Command-line argument parser for resolver binary.
 */
class ResolverArgParse {
   public:
    /**
     * @brief Parse command-line arguments.
     * @param argc Number of arguments.
     * @param argv Array of argument strings.
     * @return Parsed ResolverArgs struct, or std::nullopt if parsing failed.
     */
    static std::optional<ResolverArgs> parse(int argc, char* argv[]);

    /**
     * @brief Print usage information.
     * @param program_name Name of the program (argv[0]).
     */
    static void print_usage(const char* program_name);
};

}  // namespace rules_cc_autoconf

#pragma once

#include <optional>
#include <string>

namespace rules_cc_autoconf {

/**
 * @brief Parsed command-line arguments.
 */
struct Args {
    /** Path to JSON config file */
    std::string config_path{};

    /** Path to output config.h */
    std::string output_path{};

    /** Optional template file path */
    std::optional<std::string> template_path{};

    /** Whether to show help */
    bool show_help = false;
};

/**
 * @brief Command-line argument parser.
 *
 * Parses command-line arguments for the autoconf binary and returns
 * a structured Args object.
 */
class ArgParse {
   public:
    /**
     * @brief Parse command-line arguments.
     * @param argc Number of arguments.
     * @param argv Array of argument strings.
     * @return Parsed Args struct, or std::nullopt if parsing failed.
     */
    static std::optional<Args> parse(int argc, char* argv[]);

    /**
     * @brief Print usage information.
     * @param program_name Name of the program (argv[0]).
     */
    static void print_usage(const char* program_name);
};

}  // namespace rules_cc_autoconf

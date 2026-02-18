/**
 * @brief Runs autoconf-style checks and outputs results as JSON.
 */

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <vector>

#include "autoconf/private/checker/checker.h"
#include "autoconf/private/common/action_args.h"

using namespace rules_cc_autoconf;

namespace {

/**
 * @brief Parsed command-line arguments for checker binary.
 */
struct CheckerArgs {
    /** Path to JSON config file (required if --check is not provided) */
    std::filesystem::path config_path{};

    /** Path to JSON file containing a single check to run (required if --config
     * is not provided) */
    std::filesystem::path check_path{};

    /** Path to JSON results file to write */
    std::filesystem::path results_path{};

    /** Optional: name->file mappings for dependent check results */
    std::vector<DepMapping> dep_mappings{};

    /** Whether to show help */
    bool show_help = false;
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --config <file>        Path to JSON config file (required "
                 "if --check is not provided)\n";
    std::cout << "  --check <file>         Path to JSON file containing a "
                 "single check to run (required if --config is not provided)\n";
    std::cout << "  --results <file>       Path to JSON results file to write "
                 "(required)\n";
    std::cout << "  --dep <name>=<file>    Mapping of lookup name to result "
                 "file (can be repeated)\n";
    std::cout << "                         Example: "
                 "--dep=HAVE_FOO=/path/to/result.json\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<CheckerArgs> parse_args(int argc, char* argv[]) {
    // Expand @file response file if present
    std::vector<std::string> expanded_args;
    std::vector<char*> expanded_argv;
    int expanded_argc;
    char** expanded_argv_ptr;
    if (!expand_action_args(argc, argv, expanded_args, expanded_argv,
                            expanded_argc, expanded_argv_ptr)) {
        return std::nullopt;
    }

    CheckerArgs args;
    args.show_help = false;

    for (int i = 1; i < expanded_argc; ++i) {
        std::string arg = expanded_argv_ptr[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--config") {
            if (i + 1 < expanded_argc) {
                args.config_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --config requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--check") {
            if (i + 1 < expanded_argc) {
                args.check_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --check requires a file path" << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--results") {
            if (i + 1 < expanded_argc) {
                args.results_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --results requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--dep" || arg.rfind("--dep=", 0) == 0) {
            std::string value;
            if (arg == "--dep") {
                if (i + 1 < expanded_argc) {
                    value = std::string(expanded_argv_ptr[++i]);
                } else {
                    std::cerr << "Error: --dep requires a name=path pair"
                              << std::endl;
                    return std::nullopt;
                }
            } else {
                value = arg.substr(6);  // Skip "--dep="
            }
            if (value.empty()) {
                std::cerr << "Error: --dep value cannot be empty" << std::endl;
                return std::nullopt;
            }
            size_t eq_pos = value.find('=');
            if (eq_pos == std::string::npos || eq_pos == 0) {
                std::cerr << "Error: --dep requires name=path format, got: "
                          << value << std::endl;
                return std::nullopt;
            }
            DepMapping mapping;
            mapping.lookup_name = value.substr(0, eq_pos);
            mapping.file_path = value.substr(eq_pos + 1);
            if (mapping.file_path.empty()) {
                std::cerr << "Error: --dep file path cannot be empty"
                          << std::endl;
                return std::nullopt;
            }
            args.dep_mappings.push_back(mapping);
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    // --check requires --config (config provides compiler info, check provides
    // the check to run)
    if (args.check_path.empty() && args.config_path.empty()) {
        std::cerr << "Error: --check is required to specify which check to run"
                  << std::endl;
        return std::nullopt;
    }

    if (!args.check_path.empty() && args.config_path.empty()) {
        std::cerr << "Error: --config is required when using --check (provides "
                     "compiler information)"
                  << std::endl;
        return std::nullopt;
    }

    if (args.results_path.empty()) {
        std::cerr << "Error: --results is required" << std::endl;
        return std::nullopt;
    }

    return args;
}
}  // namespace

int main(int argc, char* argv[]) {
    std::optional<CheckerArgs> args_opt = parse_args(argc, argv);
    if (!args_opt.has_value()) {
        print_usage(argv[0]);
        return 1;
    }

    CheckerArgs args = *args_opt;

    if (args.show_help) {
        print_usage(argv[0]);
        return 0;
    }

    // If --check is provided, run a single check from file
    if (!args.check_path.empty()) {
        return Checker::run_check_from_file(args.check_path, args.config_path,
                                            args.results_path,
                                            args.dep_mappings);
    }

    // --check is required
    std::cerr << "Error: --check is required to specify which check to run."
              << std::endl;
    print_usage(argv[0]);
    return 1;
}

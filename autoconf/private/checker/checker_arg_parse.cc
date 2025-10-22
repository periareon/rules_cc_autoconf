#include "autoconf/private/checker/checker_arg_parse.h"

#include <cstdlib>
#include <iostream>
#include <sstream>

namespace rules_cc_autoconf {

void CheckerArgParse::print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout
        << "  --config <file>        Path to JSON config file (required)\n";
    std::cout << "  --results <file>       Path to JSON results file to write "
                 "(required)\n";
    std::cout << "  --check-define <name>  Run only specific check by define "
                 "name (can be specified multiple times)\n";
    std::cout << "  --required <file>     Path to JSON file containing results "
                 "from other checks (can be specified multiple times)\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<CheckerArgs> CheckerArgParse::parse(int argc, char* argv[]) {
    CheckerArgs args;
    args.show_help = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--config") {
            if (i + 1 < argc) {
                args.config_path = std::string(argv[++i]);
            } else {
                std::cerr << "Error: --config requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--results") {
            if (i + 1 < argc) {
                args.results_path = std::string(argv[++i]);
            } else {
                std::cerr << "Error: --results requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--check-define") {
            if (i + 1 < argc) {
                args.check_defines.push_back(argv[++i]);
            } else {
                std::cerr << "Error: --check-define requires a define name"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--required") {
            if (i + 1 < argc) {
                args.required_results.push_back(std::string(argv[++i]));
            } else {
                std::cerr << "Error: --required requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    if (args.config_path.empty()) {
        std::cerr << "Error: --config is required" << std::endl;
        return std::nullopt;
    }

    if (args.results_path.empty()) {
        std::cerr << "Error: --results is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

}  // namespace rules_cc_autoconf

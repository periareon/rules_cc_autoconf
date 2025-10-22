#include "autoconf/private/resolver/resolver_arg_parse.h"

#include <iostream>

namespace rules_cc_autoconf {

void ResolverArgParse::print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --results <file>       Path to JSON results file (can be "
                 "specified multiple times)\n";
    std::cout
        << "  --template <file>      Template file (config.h.in) (required)\n";
    std::cout << "  --config <file>        Optional config file (for running "
                 "additional checks from template)\n";
    std::cout << "  --output <file>        Path to output config.h file "
                 "(required)\n";
    std::cout << "  --inline <string> <file> Replace exact string in template "
                 "with file content (can be specified multiple times)\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<ResolverArgs> ResolverArgParse::parse(int argc, char* argv[]) {
    ResolverArgs args;
    args.show_help = false;

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--results") {
            if (i + 1 < argc) {
                args.results_paths.push_back(std::string(argv[++i]));
            } else {
                std::cerr << "Error: --results requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--template") {
            if (i + 1 < argc) {
                args.template_path = std::string(argv[++i]);
            } else {
                std::cerr << "Error: --template requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--output") {
            if (i + 1 < argc) {
                args.output_path = std::string(argv[++i]);
            } else {
                std::cerr << "Error: --output requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--inline") {
            if (i + 2 < argc) {
                std::string search_string = std::string(argv[++i]);
                std::string file_path = std::string(argv[++i]);
                args.inlines[search_string] = file_path;
            } else {
                std::cerr
                    << "Error: --inline requires a search string and file path"
                    << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    if (args.results_paths.empty()) {
        std::cerr << "Error: At least one --results is required" << std::endl;
        return std::nullopt;
    }

    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    if (!args.template_path.has_value()) {
        std::cerr << "Error: --template is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

}  // namespace rules_cc_autoconf

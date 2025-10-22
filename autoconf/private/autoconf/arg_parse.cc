#include "arg_parse.h"

#include <iostream>

namespace rules_cc_autoconf {

void ArgParse::print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --config <file>     Path to JSON config file (required)\n";
    std::cout
        << "  --output <file>      Path to output config.h file (required)\n";
    std::cout
        << "  --template <file>    Optional template file (config.h.in)\n";
    std::cout << "  --verbose            Verbose output\n";
    std::cout << "  --help               Show this help message\n";
    std::cout << "\nEnvironment variables:\n";
    std::cout << "  RULES_CC_AUTOCONF_DEBUG  Enable logging (any value=info, "
                 "\"debug\"=verbose)\n";
}

std::optional<Args> ArgParse::parse(int argc, char* argv[]) {
    Args args = {};
    args.show_help = false;

    // Parse command line arguments
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--config") {
            if (i + 1 < argc) {
                args.config_path = argv[++i];
            } else {
                std::cerr << "Error: --config requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--output") {
            if (i + 1 < argc) {
                args.output_path = argv[++i];
            } else {
                std::cerr << "Error: --output requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--template") {
            if (i + 1 < argc) {
                args.template_path = argv[++i];
            } else {
                std::cerr << "Error: --template requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--verbose") {
            // TODO: Implement verbose logging
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

    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

}  // namespace rules_cc_autoconf

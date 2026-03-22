/**
 * @brief Loads a manifest and generates config.h from a template.
 */

#include <fstream>
#include <iostream>
#include <map>
#include <optional>
#include <sstream>
#include <vector>

#include "autoconf/private/common/action_args.h"
#include "autoconf/private/resolver/resolver.h"
#include "autoconf/private/resolver/source_generator.h"
#include "tools/json/json.h"

using namespace rules_cc_autoconf;

namespace {

/**
 * @brief Parsed command-line arguments for resolver binary.
 */
struct ResolverArgs {
    /** Path to manifest JSON mapping define/subst names to result file paths */
    std::filesystem::path manifest_path{};

    /** Path to template file (config.h.in) (required) */
    std::filesystem::path template_path{};

    /** Path to output config.h file */
    std::filesystem::path output_path{};

    /** Inline replacements: map from search string to file path */
    std::map<std::string, std::filesystem::path> inlines{};

    /** Direct substitutions: map from placeholder name to value */
    std::map<std::string, std::string> substitutions{};

    /** Mode for processing */
    Mode mode = Mode::kDefines;

    /** Whether to show help */
    bool show_help = false;
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --manifest <file>      Path to manifest JSON mapping "
                 "define/subst names to result files (required)\n";
    std::cout
        << "  --template <file>      Template file (config.h.in) (required)\n";
    std::cout << "  --output <file>        Path to output config.h file "
                 "(required)\n";
    std::cout << "  --inline <json>        JSON object mapping search strings "
                 "to file paths for inline replacement\n";
    std::cout << "  --subst <json>         JSON object mapping placeholder "
                 "names to replacement values\n";
    std::cout
        << "  --mode <mode>          Processing mode: \"defines\" (default), "
           "\"subst\", or \"all\"\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<ResolverArgs> parse_args(int argc, char* argv[]) {
    std::vector<std::string> expanded_args;
    std::vector<char*> expanded_argv;
    int expanded_argc;
    char** expanded_argv_ptr;
    if (!expand_action_args(argc, argv, expanded_args, expanded_argv,
                            expanded_argc, expanded_argv_ptr)) {
        return std::nullopt;
    }

    ResolverArgs args;
    args.show_help = false;

    for (int i = 1; i < expanded_argc; ++i) {
        std::string arg = expanded_argv_ptr[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--manifest") {
            if (i + 1 < expanded_argc) {
                args.manifest_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --manifest requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--template") {
            if (i + 1 < expanded_argc) {
                args.template_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --template requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--mode") {
            if (i + 1 < expanded_argc) {
                std::string mode_str = std::string(expanded_argv_ptr[++i]);
                if (mode_str == "defines") {
                    args.mode = Mode::kDefines;
                } else if (mode_str == "subst") {
                    args.mode = Mode::kSubst;
                } else if (mode_str == "all") {
                    args.mode = Mode::kAll;
                } else {
                    std::cerr
                        << "Error: --mode must be \"defines\", \"subst\", "
                           "or \"all\""
                        << std::endl;
                    return std::nullopt;
                }
            } else {
                std::cerr << "Error: --mode requires a mode value" << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--output") {
            if (i + 1 < expanded_argc) {
                args.output_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --output requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--inline") {
            if (i + 1 < expanded_argc) {
                auto j =
                    nlohmann::json::parse(std::string(expanded_argv_ptr[++i]));
                for (auto& [key, value] : j.items()) {
                    args.inlines[key] = value.get<std::string>();
                }
            } else {
                std::cerr << "Error: --inline requires a JSON object"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--subst") {
            if (i + 1 < expanded_argc) {
                auto j =
                    nlohmann::json::parse(std::string(expanded_argv_ptr[++i]));
                for (auto& [key, value] : j.items()) {
                    args.substitutions[key] = value.get<std::string>();
                }
            } else {
                std::cerr << "Error: --subst requires a JSON object"
                          << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    if (args.template_path.empty()) {
        std::cerr << "Error: --template is required" << std::endl;
        return std::nullopt;
    }

    if (args.manifest_path.empty()) {
        std::cerr << "Error: --manifest is required" << std::endl;
        return std::nullopt;
    }

    return args;
}
}  // namespace

int main(int argc, char* argv[]) {
    std::optional<ResolverArgs> args_opt = parse_args(argc, argv);
    if (!args_opt.has_value()) {
        print_usage(argv[0]);
        return 1;
    }

    ResolverArgs args = *args_opt;

    if (args.show_help) {
        print_usage(argv[0]);
        return 0;
    }

    return Resolver::resolve_and_generate(
        args.manifest_path, args.template_path, args.output_path, args.inlines,
        args.substitutions, args.mode);
}

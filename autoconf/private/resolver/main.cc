/**
 * @brief Merges check results and generates config.h from a template.
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

using namespace rules_cc_autoconf;

namespace {

/**
 * @brief Parsed command-line arguments for resolver binary.
 */
struct ResolverArgs {
    /** Paths to JSON result files for cache variables (can be specified
     * multiple times) */
    std::vector<std::filesystem::path> cache_results_paths{};

    /** Paths to JSON result files for defines (can be specified multiple times)
     */
    std::vector<std::filesystem::path> define_results_paths{};

    /** Paths to JSON result files for subst values (can be specified multiple
     * times) */
    std::vector<std::filesystem::path> subst_results_paths{};

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
    std::cout << "  --cache-result <file>  Path to JSON results file for cache "
                 "variables (can be "
                 "specified multiple times)\n";
    std::cout << "  --define-result <file> Path to JSON results file for "
                 "defines (can be "
                 "specified multiple times)\n";
    std::cout << "  --subst-result <file>  Path to JSON results file for subst "
                 "values (can be "
                 "specified multiple times)\n";
    std::cout
        << "  --template <file>      Template file (config.h.in) (required)\n";
    std::cout << "  --config <file>        Optional config file (for running "
                 "additional checks from template)\n";
    std::cout << "  --output <file>        Path to output config.h file "
                 "(required)\n";
    std::cout << "  --inline <string> <file> Replace exact string in template "
                 "with file content (can be specified multiple times)\n";
    std::cout << "  --subst <name> <value> Replace @name@ placeholder with "
                 "value (can be specified multiple times)\n";
    std::cout
        << "  --mode <mode>          Processing mode: \"defines\" (default), "
           "\"subst\", or \"all\"\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<ResolverArgs> parse_args(int argc, char* argv[]) {
    // Expand @file response file if present
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
        } else if (arg == "--cache-result") {
            if (i + 1 < expanded_argc) {
                args.cache_results_paths.push_back(
                    std::string(expanded_argv_ptr[++i]));
            } else {
                std::cerr << "Error: --cache-result requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--define-result") {
            if (i + 1 < expanded_argc) {
                args.define_results_paths.push_back(
                    std::string(expanded_argv_ptr[++i]));
            } else {
                std::cerr << "Error: --define-result requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--subst-result") {
            if (i + 1 < expanded_argc) {
                args.subst_results_paths.push_back(
                    std::string(expanded_argv_ptr[++i]));
            } else {
                std::cerr << "Error: --subst-result requires a file path"
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
            if (i + 2 < expanded_argc) {
                std::string search_string = std::string(expanded_argv_ptr[++i]);
                std::string file_path = std::string(expanded_argv_ptr[++i]);
                args.inlines[search_string] = file_path;
            } else {
                std::cerr
                    << "Error: --inline requires a search string and file path"
                    << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--subst") {
            if (i + 2 < expanded_argc) {
                std::string name = std::string(expanded_argv_ptr[++i]);
                std::string value = std::string(expanded_argv_ptr[++i]);
                args.substitutions[name] = value;
            } else {
                std::cerr << "Error: --subst requires a name and value"
                          << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    // Note: result paths can be empty - the resolver will just process the
    // template without any check results

    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    if (args.template_path.empty()) {
        std::cerr << "Error: --template is required" << std::endl;
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
        args.cache_results_paths, args.define_results_paths,
        args.subst_results_paths, args.template_path, args.output_path,
        args.inlines, args.substitutions, args.mode);
}

#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "autoconf/private/autoconf/arg_parse.h"
#include "autoconf/private/autoconf/check_runner.h"
#include "autoconf/private/autoconf/config.h"
#include "autoconf/private/autoconf/debug_logger.h"
#include "autoconf/private/autoconf/source_generator.h"

using namespace rules_cc_autoconf;

/**
 * @brief Main entry point for the rules_cc_autoconf binary.
 *
 * Parses command-line arguments, loads configuration, runs checks,
 * and generates a config.h header file.
 *
 * @param argc Number of command-line arguments.
 * @param argv Array of command-line argument strings.
 * @return 0 on success, 1 on error.
 */
int main(int argc, char* argv[]) {
    // Parse arguments
    std::optional<Args> args_opt = ArgParse::parse(argc, argv);
    if (!args_opt.has_value()) {
        ArgParse::print_usage(argv[0]);
        return 1;
    }

    Args args = *args_opt;

    // Handle help
    if (args.show_help) {
        ArgParse::print_usage(argv[0]);
        return 0;
    }

    // Load configuration
    std::unique_ptr<Config> config = Config::from_file(args.config_path);

    // Get template content - either from file or use default
    std::string template_content{};
    if (args.template_path.has_value()) {
        // Read template from file
        std::ifstream template_file(*args.template_path);
        if (!template_file.is_open()) {
            std::cerr << "Error: Failed to open template file: "
                      << *args.template_path << std::endl;
            return 1;
        }
        std::stringstream buffer;
        buffer << template_file.rdbuf();
        template_content = buffer.str();
        template_file.close();
    }

    // Add automatic header checks for defines found in template but not in
    // config This mimics GNU autoconf's autoheader behavior (see
    // https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#Configuration-Headers)
    if (args.template_path.has_value()) {
        *config = config->with_template_checks(template_content);
    }

    // Create check runner and run checks
    CheckRunner runner(*config);
    std::vector<CheckResult> results = runner.run_all_checks();

    // Generate output
    SourceGenerator generator(*config, results);

    // Use default template if none was provided
    if (!args.template_path.has_value()) {
        template_content = generator.generate_default_template();
    }

    generator.generate_config_header(args.output_path, template_content);

    DebugLogger::log("Configuration header written to " + args.output_path);

    return 0;
}

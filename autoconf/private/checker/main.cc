/**
 * @brief Runs autoconf-style checks and outputs results as JSON.
 */

#include <iostream>

#include "autoconf/private/checker/checker.h"
#include "autoconf/private/checker/checker_arg_parse.h"

using namespace rules_cc_autoconf;

int main(int argc, char* argv[]) {
    std::optional<CheckerArgs> args_opt = CheckerArgParse::parse(argc, argv);
    if (!args_opt.has_value()) {
        CheckerArgParse::print_usage(argv[0]);
        return 1;
    }

    CheckerArgs args = *args_opt;

    if (args.show_help) {
        CheckerArgParse::print_usage(argv[0]);
        return 0;
    }

    if (args.check_defines.empty()) {
        std::cerr << "Error: No checks specified. Use --check-define to "
                     "specify which checks to run."
                  << std::endl;
        CheckerArgParse::print_usage(argv[0]);
        return 1;
    }

    return Checker::run_checks_by_define(args.config_path, args.check_defines,
                                         args.results_path,
                                         args.required_results);
}

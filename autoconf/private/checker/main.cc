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

    if (args.check_indices.empty()) {
        // Run all checks
        return Checker::run_checks(args.config_path, args.results_path);
    } else if (args.check_indices.size() == 1) {
        // Run single check
        return Checker::run_single_check(
            args.config_path, args.check_indices[0], args.results_path);
    } else {
        // Run multiple specific checks
        return Checker::run_checks(args.config_path, args.check_indices,
                                   args.results_path);
    }
}

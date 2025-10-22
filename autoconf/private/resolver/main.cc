#include <iostream>

#include "autoconf/private/resolver/resolver.h"
#include "autoconf/private/resolver/resolver_arg_parse.h"

using namespace rules_cc_autoconf;

int main(int argc, char* argv[]) {
    std::optional<ResolverArgs> args_opt = ResolverArgParse::parse(argc, argv);
    if (!args_opt.has_value()) {
        ResolverArgParse::print_usage(argv[0]);
        return 1;
    }

    ResolverArgs args = *args_opt;

    if (args.show_help) {
        ResolverArgParse::print_usage(argv[0]);
        return 0;
    }

    return Resolver::resolve_and_generate(
        args.results_paths, args.package_info_path, args.template_path,
        args.config_path, args.output_path, args.output_results_path);
}

#pragma once

#ifndef _WIN32
#include <filesystem>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

// Run a nonempty argument vector without a shell. A normal nonzero exit is a
// rejected probe, except status 127, which POSIX also uses for startup failure.
// Startup/wait errors and fatal signals throw std::system_error instead of
// reporting a missing capability.
// Empty output paths inherit the corresponding stream.
int run_process(const std::vector<std::string>& args,
                const std::filesystem::path& stdout_path,
                const std::filesystem::path& stderr_path);

}  // namespace rules_cc_autoconf
#endif

#pragma once

#include <filesystem>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "autoconf/private/checker/check.h"

namespace rules_cc_autoconf {

/**
 * @brief Configuration for autoconf checks.
 *
 * Contains compiler information, flags, and checks to perform.
 */
struct Config {
    /** Path to C compiler */
    std::string c_compiler{};

    /** Path to C++ compiler */
    std::string cpp_compiler{};

    /** C compiler flags */
    std::vector<std::string> c_flags{};

    /** C++ compiler flags */
    std::vector<std::string> cpp_flags{};

    /** C linker flags */
    std::vector<std::string> c_link_flags{};

    /** C++ linker flags */
    std::vector<std::string> cpp_link_flags{};

    /** Path to linker tool */
    std::string linker{};

    /** Compiler type (e.g., "msvc", "gcc", "clang") */
    std::string compiler_type{};

    /** Array of checks to perform */
    std::vector<Check> checks{};

    /**
     * @brief Load configuration from a JSON file.
     * @param config_path Path to the JSON configuration file.
     * @return Unique pointer to the loaded Config.
     * @throws std::runtime_error if the file cannot be opened or parsed.
     */
    static std::unique_ptr<Config> from_file(
        const std::filesystem::path& config_path);
};

}  // namespace rules_cc_autoconf

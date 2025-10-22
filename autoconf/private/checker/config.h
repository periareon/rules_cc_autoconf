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
 * Contains compiler information, flags, checks to perform, and package
 * metadata.
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

    /** Package name for PACKAGE_NAME define */
    std::string package_name{};

    /** Package version for PACKAGE_VERSION define */
    std::string package_version{};

    /** Additional custom defines */
    std::map<std::string, std::string> defines{};

    /**
     * @brief Load configuration from a JSON file.
     * @param config_path Path to the JSON configuration file.
     * @return Unique pointer to the loaded Config.
     * @throws std::runtime_error if the file cannot be opened or parsed.
     */
    static std::unique_ptr<Config> from_file(
        const std::filesystem::path& config_path);

    /**
     * @brief Create a new Config with additional checks based on a template
     * string.
     *
     * This method mimics GNU autoconf's behavior where autoheader generates
     * a template file containing #undef statements for standard headers, and
     * configure automatically checks for those headers. When a template is
     * provided, this method extracts header defines (HAVE_*_H) from the
     * template and adds corresponding header checks if they're not already in
     * the config.
     *
     * Reference:
     * https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#Configuration-Headers
     *
     * @param template_content Template content as a string (e.g., contents of
     * config.h.in).
     * @return A new Config object with additional checks added.
     */
    Config with_template_checks(const std::string& template_content) const;
};

}  // namespace rules_cc_autoconf

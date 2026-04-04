#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "autoconf/private/resolver/source_generator.h"

namespace rules_cc_autoconf {

/**
 * @brief Library for resolving autoconf results and generating headers.
 *
 * Loads check results via a manifest JSON (which maps define/subst names to
 * flat result file paths and rendering metadata), then generates config.h
 * headers from a template.
 */
class Resolver {
   public:
    /**
     * @brief Resolve results from a manifest and generate a header file.
     *
     * The manifest JSON maps consumer names to flat result file paths and
     * rendering metadata (e.g. unquote). Each flat result file contains only
     * {success, value, type}.
     *
     * @param manifest_path Path to the manifest JSON file.
     * @param template_path Path to template file (config.h.in) (required).
     * @param output_path Path where config.h will be written.
     * @param inlines Map from search strings to file paths for inline
     * replacements.
     * @param substitutions Map from placeholder names to values for direct
     * @VAR@ substitution.
     * @param mode Processing mode (default: kDefines).
     * @return 0 on success, 1 on error.
     */
    static int resolve_and_generate(
        const std::filesystem::path& manifest_path,
        const std::filesystem::path& template_path,
        const std::filesystem::path& output_path,
        const std::map<std::string, std::filesystem::path>& inlines = {},
        const std::map<std::string, std::string>& substitutions = {},
        Mode mode = Mode::kDefines);
};

}  // namespace rules_cc_autoconf

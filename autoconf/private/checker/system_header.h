#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Find the absolute path of a system header by running the preprocessor.
 *
 * Compiles `#include <header>` with the `-E` / `/E` flag and parses the
 * `#line` markers in the output to locate the system header's real path.
 *
 * @param compiler Path to the compiler executable.
 * @param flags Compiler flags (toolchain flags only).
 * @param compiler_type Compiler type string (e.g., "msvc-cl", "gcc").
 * @param header Header name (e.g., "stddef.h").
 * @param source_id Unique identifier for temporary source files.
 * @param source_dir Directory for temporary source files.
 * @return Absolute path to the system header, or nullopt if not found.
 */
std::optional<std::filesystem::path> find_system_header_path(
    const std::string& compiler, const std::vector<std::string>& flags,
    const std::string& compiler_type, const std::string& header,
    const std::string& source_id, const std::filesystem::path& source_dir);

/**
 * @brief Parse preprocessor output to extract the path of an included header.
 *
 * Handles both GCC/Clang format (`# 1 "/path/header.h" 1 3 4`) and
 * MSVC format (`#line 1 "C:\\path\\header.h"`).
 *
 * @param preprocessor_output The full preprocessor `-E` output.
 * @param header The header name to search for (e.g., "stddef.h").
 * @return The absolute path found in the line markers, or nullopt.
 */
std::optional<std::filesystem::path> parse_line_markers(
    const std::string& preprocessor_output, const std::string& header);

/**
 * @brief Read the entire content of a file into a string.
 *
 * @param path Path to the file to read.
 * @return File content, or nullopt if the file cannot be read.
 */
std::optional<std::string> read_file_content(const std::filesystem::path& path);

}  // namespace rules_cc_autoconf

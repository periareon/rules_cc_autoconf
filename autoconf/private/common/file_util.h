#pragma once

#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>

namespace rules_cc_autoconf {

#ifdef _WIN32
/**
 * @brief Convert a path to an absolute `\\?\`-prefixed wide string.
 *
 * The `\\?\` prefix tells Windows to skip MAX_PATH (260 char) enforcement.
 * It works for paths of any length so we apply it unconditionally on Windows,
 * avoiding fragile length checks that can miss relative-path expansion.
 *
 * @param path Path to convert (may be relative; will be made absolute).
 * @return Wide string with `\\?\` prefix suitable for Windows wide-char APIs.
 */
inline std::wstring to_extended_length_path(const std::string& path) {
    auto abs = std::filesystem::absolute(path).make_preferred();
    return L"\\\\?\\" + abs.native();
}
#endif

/**
 * @brief Open an input file stream, bypassing Windows MAX_PATH limits.
 *
 * On Windows the `\\?\` extended-length prefix is always applied so that
 * every path works regardless of length.  On other platforms this is
 * equivalent to `std::ifstream(path)`.
 */
inline std::ifstream open_ifstream(const std::string& path) {
#ifdef _WIN32
    return std::ifstream(to_extended_length_path(path));
#else
    return std::ifstream(path);
#endif
}

/**
 * @brief Open an output file stream, bypassing Windows MAX_PATH limits.
 *
 * On Windows the `\\?\` extended-length prefix is always applied so that
 * every path works regardless of length.  On other platforms this is
 * equivalent to `std::ofstream(path)`.
 */
inline std::ofstream open_ofstream(const std::string& path) {
#ifdef _WIN32
    return std::ofstream(to_extended_length_path(path));
#else
    return std::ofstream(path);
#endif
}

/**
 * @brief Check whether a file exists, bypassing Windows MAX_PATH limits.
 *
 * On Windows the `\\?\` extended-length prefix is always applied.
 * On other platforms this is equivalent to `std::filesystem::exists()`.
 */
inline bool file_exists(const std::string& path) {
    std::error_code ec;
#ifdef _WIN32
    return std::filesystem::exists(
        std::filesystem::path(to_extended_length_path(path)), ec);
#else
    return std::filesystem::exists(std::filesystem::path(path), ec);
#endif
}

/**
 * @brief Remove a file, bypassing Windows MAX_PATH limits.
 *
 * On Windows the `\\?\` extended-length prefix is always applied.
 * On other platforms this is equivalent to `std::filesystem::remove(path, ec)`.
 */
inline bool file_remove(const std::string& path, std::error_code& ec) {
#ifdef _WIN32
    return std::filesystem::remove(
        std::filesystem::path(to_extended_length_path(path)), ec);
#else
    return std::filesystem::remove(std::filesystem::path(path), ec);
#endif
}

// -- std::filesystem::path overloads ------------------------------------------

inline bool file_exists(const std::filesystem::path& path) {
    return file_exists(path.string());
}

inline std::ifstream open_ifstream(const std::filesystem::path& path) {
    return open_ifstream(path.string());
}

inline std::ofstream open_ofstream(const std::filesystem::path& path) {
    return open_ofstream(path.string());
}

inline bool file_remove(const std::filesystem::path& path,
                        std::error_code& ec) {
    return file_remove(path.string(), ec);
}

}  // namespace rules_cc_autoconf

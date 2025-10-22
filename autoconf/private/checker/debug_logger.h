#pragma once

#include <algorithm>
#include <cstdlib>
#include <iostream>
#include <string>

namespace rules_cc_autoconf {

/**
 * @brief Utility class for debug logging controlled by environment variable.
 *
 * Provides logging functionality following RUST_LOG convention, controlled by
 * the RULES_CC_AUTOCONF_DEBUG environment variable:
 * - Level 0 (unset): No debug output (errors only)
 * - Level 1 (set to anything): Info level output (log, warn)
 * - Level 2 (set to "debug"): Debug level output (debug, log, warn)
 *
 * Error messages are always shown regardless of debug level.
 */
class DebugLogger {
   public:
    /**
     * @brief Get the current debug level from environment variable.
     *
     * Follows RUST_LOG convention:
     * - Returns 0 if RULES_CC_AUTOCONF_DEBUG is unset
     * - Returns 1 if RULES_CC_AUTOCONF_DEBUG is set to any value (info level)
     * - Returns 2 if RULES_CC_AUTOCONF_DEBUG is set to "debug" (debug level)
     *
     * @return Debug level: 0 if unset, 2 if "debug", 1 otherwise.
     */
    static int get_debug_level() {
        static int level = []() {
            const char* env = std::getenv("RULES_CC_AUTOCONF_DEBUG");
            if (env == nullptr) {
                return 0;
            }
            std::string level_str(env);
            // Check for "debug" (case-insensitive)
            std::string lower_level;
            std::transform(level_str.begin(), level_str.end(),
                           std::back_inserter(lower_level), ::tolower);
            if (lower_level == "debug" || lower_level == "2") {
                return 2;
            }
            // Any other value (including empty) enables info level
            return 1;
        }();
        return level;
    }

    /**
     * @brief Check if info-level logging is enabled (level >= 1).
     * @return true if RULES_CC_AUTOCONF_DEBUG environment variable is set to
     * any value.
     */
    static bool is_debug_enabled() { return get_debug_level() >= 1; }

    /**
     * @brief Check if debug-level logging is enabled (level >= 2).
     * @return true if RULES_CC_AUTOCONF_DEBUG environment variable is set to
     * "debug".
     */
    static bool is_verbose_debug_enabled() { return get_debug_level() >= 2; }

    /**
     * @brief Log an info message (only shown when info level is enabled).
     * @tparam T Type of the message (must be streamable to std::cout).
     * @param message The message to log.
     */
    template <typename T>
    static void log(const T& message) {
        if (is_debug_enabled()) {
            std::cout << message << std::endl;
        }
    }

    /**
     * @brief Log a debug message (only shown when debug level is enabled).
     * @tparam T Type of the message (must be streamable to std::cerr).
     * @param message The debug message to log.
     */
    template <typename T>
    static void debug(const T& message) {
        if (is_verbose_debug_enabled()) {
            std::cerr << "Debug: " << message << std::endl;
        }
    }

    /**
     * @brief Log a warning message (only shown when info level is enabled).
     * @tparam T Type of the message (must be streamable to std::cerr).
     * @param message The warning message to log.
     */
    template <typename T>
    static void warn(const T& message) {
        if (is_debug_enabled()) {
            std::cerr << "Warning: " << message << std::endl;
        }
    }

    /**
     * @brief Log an error message (always shown).
     * @tparam T Type of the message (must be streamable to std::cerr).
     * @param message The error message to log.
     */
    template <typename T>
    static void error(const T& message) {
        std::cerr << "Error: " << message << std::endl;
    }
};

}  // namespace rules_cc_autoconf

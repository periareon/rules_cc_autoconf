#pragma once

#include <optional>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Type of configuration check to perform.
 */
enum class CheckType {
    kUnknown,     ///< An unknown check.
    kFunction,    ///< Check for function
    kLib,         ///< Check for function in library
    kType,        ///< Check for type
    kCompile,     ///< Check if code compiles
    kLink,        ///< Check if code compiles and links
    kDefine,      ///< Directly apply the define with the given value
    kM4Variable,  ///< M4_VARIABLE - compute value for requires but don't
                  ///< generate output (can be subst)
    kSizeof,      ///< Determine size of type
    kAlignof,     ///< Determine alignment of type
    kComputeInt,  ///< Compute integer value
    kEndian,      ///< Determine endianness
    kDecl,        ///< Check for declaration
    kMember,      ///< Check for struct/union member
};

/**
 * @brief Convert CheckType to string representation.
 * @param type The CheckType enum value.
 * @return String representation of the type (e.g., "subst", "header").
 */
std::string check_type_to_string(CheckType type);

/**
 * @brief Determine if a CheckType is a define (not kM4Variable).
 * @param type The CheckType enum value.
 * @return True if this is a define, false otherwise.
 */
bool check_type_is_define(CheckType type);

/**
 * @brief Configuration check specification.
 *
 * Represents a single autoconf-style check to be performed.
 * Parsed from JSON configuration.
 */
class Check {
   public:
    /**
     * @brief Parse a Check from JSON.
     * @param json_data Opaque pointer to JSON data (nlohmann::json).
     * @return Check object, or std::nullopt if parsing failed.
     */
    static std::optional<Check> from_json(const void* json_data);

    /**
     * @brief Get the type of this check.
     * @return The CheckType enum value.
     */
    CheckType type() const { return type_; }

    /**
     * @brief Get the name of the item being checked.
     * @return The name (e.g., header name, function name, type name).
     */
    const std::string& name() const { return name_; }

    /**
     * @brief Get the preprocessor define name for this check.
     * @return Optional define name (e.g., "HAVE_STDIO_H"), or std::nullopt if
     * not provided.
     */
    const std::optional<std::string>& define() const { return define_; }

    /**
     * @brief Get the programming language for this check.
     * @return The language string ("c" or "cpp").
     */
    const std::string& language() const { return language_; }

    /**
     * @brief Get the optional custom code for this check.
     * @return Optional string containing custom test code, or std::nullopt if
     * not provided.
     */
    const std::optional<std::string>& code() const { return code_; }

    /**
     * @brief Get the optional file path for this check.
     * @return Optional string containing path to test file, or std::nullopt if
     * not provided.
     */
    const std::optional<std::string>& file_path() const { return file_path_; }

    /**
     * @brief Get the optional define value when check succeeds.
     * @return Optional string containing the value to use for the define if the
     * check succeeds, or std::nullopt if not provided.
     */
    const std::optional<std::string>& define_value() const {
        return define_value_;
    }

    /**
     * @brief Get the optional define value when check fails.
     * @return Optional string containing the value to use for the define if the
     * check fails, or std::nullopt if not provided.
     */
    const std::optional<std::string>& define_value_fail() const {
        return define_value_fail_;
    }

    /**
     * @brief Get the optional library name for library checks.
     * @return Optional string containing the library name (without -l prefix),
     * or std::nullopt if not provided.
     */
    const std::optional<std::string>& library() const { return library_; }

    /**
     * @brief Get the optional list of required define names.
     * @return Optional vector of define names that must be successful before
     * this check runs, or std::nullopt if not provided.
     */
    const std::optional<std::vector<std::string>>& required_defines() const {
        return requires_;
    }

    /**
     * @brief Get the optional condition for conditional defines/substs.
     * @return Optional string containing the name of the define to check,
     * or std::nullopt if not a conditional check.
     */
    const std::optional<std::string>& condition() const { return condition_; }

    /**
     * @brief Get the optional list of compile_defines file paths to include in
     * compilation.
     * @return Optional vector of result file paths from previous checks to read
     * and extract defines from, or std::nullopt if not provided.
     */
    const std::optional<std::vector<std::string>>& compile_defines() const {
        return compile_defines_;
    }

    /**
     * @brief Get the substitution variable name for this check.
     * @return Optional subst name (e.g., "HAVE_PRINTF"), or std::nullopt if not
     * a subst.
     */
    const std::optional<std::string>& subst() const { return subst_; }

    /**
     * @brief Get whether this is an unquoted define (AC_DEFINE_UNQUOTED).
     * @return True if this is AC_DEFINE_UNQUOTED, false otherwise.
     */
    bool unquote() const { return unquote_; }

   private:
    std::string name_{};                   /// Name (e.g., header/function name)
    std::optional<std::string> define_{};  /// Optional preprocessor define name
    std::string language_{};               /// Language ("c" or "cpp")
    std::optional<std::string> code_{};    /// Optional custom code
    std::optional<std::string> file_path_{};     /// Optional file path
    std::optional<std::string> define_value_{};  /// Value if check succeeds
    std::optional<std::string> define_value_fail_{};  /// Value if check fails
    std::optional<std::string> library_{};  /// Library name for lib checks
    std::optional<std::vector<std::string>> requires_{};  /// Required defines
    std::optional<std::string>
        condition_{};  /// Condition for conditional checks
    std::optional<std::vector<std::string>>
        compile_defines_{};  /// Defines to include in compilation code
    CheckType type_{};       /// Type of check
    std::optional<std::string>
        subst_{};          /// Optional substitution variable name
    bool unquote_{false};  /// Whether this is AC_DEFINE_UNQUOTED (affects empty
                           /// value rendering)

    /**
     * @brief Private constructor (use from_json to create).
     */
    Check(CheckType type, std::string name, std::optional<std::string> define,
          std::string language)
        : name_(std::move(name)),
          define_(std::move(define)),
          language_(std::move(language)),
          type_(type) {}
};

}  // namespace rules_cc_autoconf

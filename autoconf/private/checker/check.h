#pragma once

#include <optional>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Type of configuration check to perform.
 */
enum class CheckType {
    kHeader,      ///< Check for header file
    kFunction,    ///< Check for function
    kLib,         ///< Check for function in library
    kSymbol,      ///< Check for preprocessor symbol
    kType,        ///< Check for type
    kCompile,     ///< Check if code compiles
    kDefine,      ///< Directly apply the define with the given value
    kSizeof,      ///< Determine size of type
    kAlignof,     ///< Determine alignment of type
    kComputeInt,  ///< Compute integer value
    kEndian,      ///< Determine endianness
    kDecl,        ///< Check for declaration
    kMember,      ///< Check for struct/union member
};

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
     * @return The define name (e.g., "HAVE_STDIO_H").
     */
    const std::string& define() const { return define_; }

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

   private:
    std::string name_{};                 /// Name (e.g., header/function name)
    std::string define_{};               /// Preprocessor define name
    std::string language_{};             /// Language ("c" or "cpp")
    std::optional<std::string> code_{};  /// Optional custom code
    std::optional<std::string> file_path_{};     /// Optional file path
    std::optional<std::string> define_value_{};  /// Value if check succeeds
    std::optional<std::string> define_value_fail_{};  /// Value if check fails
    std::optional<std::string> library_{};  /// Library name for lib checks
    std::optional<std::vector<std::string>> requires_{};  /// Required defines
    CheckType type_{};                                    /// Type of check

    /**
     * @brief Private constructor (use from_json to create).
     */
    Check(CheckType type, std::string name, std::string define,
          std::string language)
        : name_(std::move(name)),
          define_(std::move(define)),
          language_(std::move(language)),
          type_(type) {}
};

}  // namespace rules_cc_autoconf

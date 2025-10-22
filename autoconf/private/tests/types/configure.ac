AC_INIT([test_types], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for types
# size_t without header - should not be found in our cross-compile context
AC_CHECK_TYPES([size_t])
# Types from stdint.h
AC_CHECK_TYPES([int8_t, int64_t], [], [], [#include <stdint.h>])
# Non-existent type
AC_CHECK_TYPES([nonexistent_type_xyz])

AC_OUTPUT

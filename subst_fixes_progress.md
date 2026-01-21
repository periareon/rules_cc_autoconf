# Subst Fixes Progress

## Completed Modules

1. **call_once** - Added defaults target with thread-related subst variables
2. **canonicalize** - Added HAVE_CANONICALIZE_FILE_NAME="0" and REPLACE_REALPATH="1" to main target
3. **cbrt** - Added subst=True to AC_CHECK_FUNC, added defaults with LIBM variables (CBRT_LIBM, FABS_LIBM, FREXP_LIBM, ISNAND_LIBM, LDEXP_LIBM, POW_LIBM)
4. **cbrtf** - Added subst=True to AC_CHECK_FUNC
5. **cbrtl** - Added subst=True to AC_CHECK_FUNC
6. **ceil** - Added subst=True to AC_CHECK_FUNC, added defaults with CEIL_LIBM=""
7. **ceilf** - Added subst=True to AC_CHECK_DECL, added defaults with CEILF_LIBM="", added math_h dependency for REPLACE_HUGE_VAL and REPLACE_NAN

## Patterns Identified

### Math Functions (cbrt, ceil, cos, etc.)
- Need `subst=True` on `AC_CHECK_FUNC` or `AC_CHECK_DECL` calls
- Need `*_LIBM=""` variables in defaults (e.g., CBRT_LIBM, CEIL_LIBM, etc.)
- May need `math_h` dependency for REPLACE_HUGE_VAL and REPLACE_NAN (for float variants)

### Thread Functions (call_once, cnd, etc.)
- Need defaults with thread-related variables:
  - BROKEN_THRD_*, HAVE_THREADS_H, HAVE_THREAD_LOCAL
  - LIBPMULTITHREAD, LIBPTHREAD, LIBSTDTHREAD, etc.
  - REPLACE_* variables for all thread functions

### System Functions (canonicalize, etc.)
- May need conditional REPLACE_* variables based on platform
- Some functions need explicit subst values even when conditionals exist

## Key Fix Pattern

### When subst value is not being replaced
**First attempt**: Find where that value is defined for config.h and set `subst=True` on that definition.

Example: `HAVE_SIGSET_T` was a placeholder in subst.h. The fix was to add `subst=True` to the `AC_DEFINE` for `HAVE_SIGSET_T` in `signal_h_sigset_t`.

This is the fix for most cases - if a value appears in config.h but not in subst.h, add `subst=True` to the check/define that creates it.

**For subst-only variables** (not in config.h): If conditional `AC_SUBST` isn't working correctly, use defaults target to set the value unconditionally.

## Complications

### AC_SUBST with condition parameter
- Found that `AC_SUBST` with `condition` parameter may not always work correctly
- Workaround: Set values unconditionally in main target or defaults target, OR add `subst=True` to the AC_DEFINE that creates the value
- Example: `canonicalize` had `HAVE_CANONICALIZE_FILE_NAME` with condition, but had to set it unconditionally to "0"

### LIBM Variables
- Many math functions need `*_LIBM=""` variables
- These are typically empty strings on modern systems
- Need to be added to defaults targets for each math function module

### Dependencies
- **UPDATE**: No targets should depend on `:with_defaults` - subst generation now always includes defaults
- Modules should depend on base targets (e.g., `math_h` not `math_h:with_defaults`)
- Tests can still use `:with_defaults` targets, but module BUILD files should not
- Some modules need `math_h` dependency for REPLACE_HUGE_VAL and REPLACE_NAN (especially float variants)

## Remaining Modules

See TODO list for full list of remaining modules to fix.

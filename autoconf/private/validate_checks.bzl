"""Aspect that validates check JSON at analysis time using AutoconfCheck."""

load("//autoconf/private:check_info.bzl", "AutoconfCheckInfo")

def _validate_checks_impl(target, ctx):
    checks = getattr(ctx.rule.attr, "checks", None)
    if checks == None:
        return []

    for check_json_str in checks:
        check = json.decode(check_json_str)

        if "name" not in check:
            fail("Check in target {} is missing required field 'name'.".format(
                target.label,
            ))
        if "type" not in check:
            fail("Check '{}' in target {} is missing required field 'type'.".format(
                check.get("name", "<unnamed>"),
                target.label,
            ))

        # Construct the provider to validate field names, types, and constraints.
        # The instance is discarded immediately; this is purely a validation step.
        AutoconfCheckInfo(**check)

    return []

validate_checks_aspect = aspect(
    implementation = _validate_checks_impl,
    attr_aspects = ["deps"],
)

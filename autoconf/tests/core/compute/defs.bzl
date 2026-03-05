load("//autoconf:checks.bzl", "checks")

RANGE_BEGIN = -1024
RANGE_END = 1025

def _gen_template_and_src_impl(ctx):
    src = ctx.actions.declare_file("gen_test_compute.c")

    checks = "\n".join(["""assert(DEFINE_{} == {});""".format(str(i).replace("-", "_"), str(i)) for i in range(RANGE_BEGIN, RANGE_END, 1)])

    contents = '''#undef NDEBUG
#include <assert.h>
#include "template.h"
int main(){{
{}
return 0;
}}'''.format(checks)
    ctx.actions.write(src, contents)

    template = ctx.actions.declare_file("template.h.in")
    template_contents = "\n".join(["#undef DEFINE_{}".format(i).replace("-", "_") for i in range(RANGE_BEGIN, RANGE_END, 1)])
    ctx.actions.write(template, template_contents + "\n")

    return [DefaultInfo(files = depset([src, template])), OutputGroupInfo(c = depset([src]), template = depset([template]))]

gen_template_and_src = rule(
    implementation = _gen_template_and_src_impl,
)

"""# CcAutoconfInfo

Public API for external rules that produce autoconf-compatible check results.

The ``CcAutoconfInfo`` provider constructor accepts defaults for every field
except ``owner``, so callers only need to supply the fields they care about:

```python
load("@rules_cc_autoconf//autoconf:cc_autoconf_info.bzl",
     "CcAutoconfInfo", "encode_result")

def _my_rule_impl(ctx):
    result = ctx.actions.declare_file("{}/MY_DEFINE.result.json".format(ctx.label.name))
    ctx.actions.write(output = result, content = encode_result("hello"))
    return [CcAutoconfInfo(owner = ctx.label, define_results = {"MY_DEFINE": result})]
```
"""

load(
    "//autoconf/private:autoconf_config.bzl",
    _encode_result = "encode_result",
)
load(
    "//autoconf/private:providers.bzl",
    _CcAutoconfInfo = "CcAutoconfInfo",
)

CcAutoconfInfo = _CcAutoconfInfo
encode_result = _encode_result

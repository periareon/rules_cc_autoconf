"""# providers"""

CcAutoconfInfo = provider(
    doc = "A provider containing autoconf configuration and results.",
    fields = {
        "deps": "depset[CcAutoconfInfo]: Dependencies of the current info target.",
        "owner": "Label: The label of the owner of the results.",
        "results": "dict[str, File]: A map of define names to JSON files containing the defines produced by `CcAutoconfCheck` actions. Each define name must be unique.",
    },
)

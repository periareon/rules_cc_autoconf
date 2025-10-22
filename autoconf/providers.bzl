"""# providers"""

CcAutoconfInfo = provider(
    doc = "A provider containing autoconf configuration and results.",
    fields = {
        "package_info": "File: Package info JSON used when generating headers.",
        "results": "depset[File]: A depset of JSON files containing the defines produced by `CcAutoconfCheck` actions.",
    },
)

#pragma once

// Suppress warnings from nlohmann/json library on MSVC
#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4820)  // padding in struct
#pragma warning(disable : 4626)  // assignment operator implicitly deleted
#pragma warning(disable : 5027)  // move assignment operator implicitly deleted
#pragma warning(disable : 4623)  // default constructor implicitly deleted
#pragma warning(disable : 4625)  // copy constructor implicitly deleted
#pragma warning(disable : 4514)  // unreferenced inline function removed
#pragma warning(disable : 5045)  // Compiler will insert Spectre mitigation for
                                 // memory load if /Qspectre switch specified
#endif

#include <nlohmann/json.hpp>

#ifdef _MSC_VER
#pragma warning(pop)
#endif

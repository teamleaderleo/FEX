#include <common/GeneratorInterface.h>

#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>

using EnumerateInstanceVersionSignature = VkResult(uint32_t*);
using XlibPresentationSupportSignature = VkBool32(VkPhysicalDevice, uint32_t, Display*, VisualID);

template<typename>
struct fex_gen_type {};

template<>
struct fex_gen_type<VkPhysicalDevice_T> : fexgen::opaque_type {};
template<>
struct fex_gen_type<_XDisplay> : fexgen::opaque_type {};

template<>
struct fex_gen_type<EnumerateInstanceVersionSignature> {};
template<>
struct fex_gen_type<XlibPresentationSupportSignature> {};

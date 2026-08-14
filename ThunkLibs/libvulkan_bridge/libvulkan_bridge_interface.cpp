#include <common/GeneratorInterface.h>

#define VK_USE_64_BIT_PTR_DEFINES 0
#define VK_USE_PLATFORM_XLIB_KHR
#include <vulkan/vulkan.h>

VkResult FEXBridgeEnumerateInstanceVersion(uint32_t*);
VkBool32 FEXBridgeXlibPresentationSupport(VkPhysicalDevice, uint32_t, Display*, VisualID);

template<auto>
struct fex_gen_config : fexgen::indirect_guest_calls {};

template<typename>
struct fex_gen_type {};

template<>
struct fex_gen_type<VkPhysicalDevice_T> : fexgen::opaque_type {};
template<>
struct fex_gen_type<_XDisplay> : fexgen::opaque_type {};

template<>
struct fex_gen_config<FEXBridgeEnumerateInstanceVersion> {};
template<>
struct fex_gen_config<FEXBridgeXlibPresentationSupport> {};

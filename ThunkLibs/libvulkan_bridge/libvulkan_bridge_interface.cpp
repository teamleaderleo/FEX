#include <common/GeneratorInterface.h>
#include <vulkan/vulkan.h>

VkResult FEXBridgeEnumerateInstanceVersion(uint32_t*);

template<auto>
struct fex_gen_config : fexgen::indirect_guest_calls {};

template<>
struct fex_gen_config<FEXBridgeEnumerateInstanceVersion> {};

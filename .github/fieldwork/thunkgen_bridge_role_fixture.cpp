#include <common/GeneratorInterface.h>

template<auto>
struct fex_gen_config {
  unsigned version = 1;
};

template<typename>
struct fex_gen_type {};

// Unpacker-only registration: ordinary callback parameter with no matching
// indirect-call signature.
using FixtureCallbackOnly = void (*)(int);
int fixture_register_callback_only(FixtureCallbackOnly callback);
template<>
struct fex_gen_config<fixture_register_callback_only> {};

// This callback signature also appears as an indirect API function below.
// Canonical-signature deduplication must OR the two registrations to produce
// caller=1, unpacker=1.
using FixtureSharedCallback = int (*)(int);
int fixture_register_shared_callback(FixtureSharedCallback callback, int value);
template<>
struct fex_gen_config<fixture_register_shared_callback> {};

int fixture_indirect(int value);
namespace internal {
template<auto>
struct fex_gen_config : fexgen::generate_guest_symtable, fexgen::indirect_guest_calls {};

template<>
struct fex_gen_config<fixture_indirect> {};
} // namespace internal
